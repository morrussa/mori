from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_puppet_path(repo_root: Path) -> Path:
    return (repo_root / "model" / "inochi2d" / "puppets" / "aka" / "Aka.inx").resolve()


def _resolve_live_dir(*, workdir: Path, live_dir: str) -> Path:
    p = Path(live_dir).expanduser()
    return (p if p.is_absolute() else (workdir / p)).resolve()


def parse_args() -> argparse.Namespace:
    repo_root = _repo_root()
    p = argparse.ArgumentParser(
        prog="run-bili-vtuber-love2d",
        description="Launch: Love2D frontend (Inox2D) + vtuber.py (bilibili danmaku -> subtitles + TTS wav playback).",
    )
    p.add_argument("--workdir", default=str(repo_root), help="Passed to vtuber.py --workdir.")
    p.add_argument("--live-dir", default="live", help="Passed to vtuber.py --live-dir (relative to --workdir if not absolute).")

    p.add_argument("--bilibili-room-url", default="", help="Passed to vtuber.py --bilibili-room-url.")
    p.add_argument("--bilibili-room-id", type=int, default=0, help="Passed to vtuber.py --bilibili-room-id.")
    p.add_argument("--bilibili-interval", type=float, default=2.0, help="Passed to vtuber.py --bilibili-interval.")
    p.add_argument("--bilibili-catchup", type=int, default=0, help="Passed to vtuber.py --bilibili-catchup.")
    p.add_argument("--exit-when-offline", action="store_true", help="Passed to vtuber.py --bilibili-exit-when-offline.")

    p.add_argument("--tts-cuda", action="store_true", help="Require CUDA TTS runtime (vtuber.py --tts-cuda).")
    p.add_argument("--tts-root", default="", help="Optional override for vtuber.py --tts-root.")

    p.add_argument("--love-bin", default="love", help="Path to Love2D executable (default: love).")
    p.add_argument("--puppet", default=str(_default_puppet_path(repo_root)), help="Path to .inx/.inp puppet for Love2D.")
    p.add_argument("--mapping", default="", help="Optional MORI_MAPPING_PATH for Love2D (.mori-map / .lua).")
    p.add_argument(
        "--mouse-look",
        default="",
        help="Set MORI_MOUSE_LOOK for Love2D (on/off/1/0). Default is off unless your frontend config overrides it.",
    )
    p.add_argument(
        "--skip-prepare",
        action="store_true",
        help="Skip auto steps (build inox2d FFI / install example puppet when missing).",
    )
    return p.parse_args()


def _has_inox2d_lib(repo_root: Path) -> bool:
    candidates = [
        (repo_root / "model" / "inochi2d" / "native" / "libmori_inox2d.so").resolve(),
        (repo_root / "mori_live2d" / "native" / "inox2d_ffi" / "target" / "release" / "libmori_inox2d_ffi.so").resolve(),
    ]
    return any(p.is_file() for p in candidates)


def _run_prepare_step(cmd: list[str], *, cwd: Path) -> None:
    print("prep> " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True, text=True)


def _check_python_deps() -> None:
    try:
        import lupa  # noqa: F401
    except Exception as e:
        raise ModuleNotFoundError(
            "Missing dependency: lupa. Activate your venv, then run: `pip install -r requirements.txt`"
        ) from e


def _resolve_love_bin(love_bin: str) -> str:
    s = str(love_bin or "").strip() or "love"
    if os.sep in s or (os.altsep and os.altsep in s):
        return str(Path(s).expanduser().resolve())
    found = shutil.which(s)
    return found or s


def main() -> int:
    args = parse_args()
    repo_root = _repo_root()

    _check_python_deps()

    workdir = Path(args.workdir).expanduser().resolve()
    live_dir = _resolve_live_dir(workdir=workdir, live_dir=str(args.live_dir))
    puppet_path = Path(args.puppet).expanduser().resolve()

    if not bool(args.skip_prepare):
        if not puppet_path.exists() and puppet_path == _default_puppet_path(repo_root):
            _run_prepare_step(
                [sys.executable, "-m", "mori_live2d.cli", "install-models", "--models", "aka"],
                cwd=repo_root,
            )
        if not _has_inox2d_lib(repo_root):
            _run_prepare_step([sys.executable, "-m", "mori_live2d.cli", "build-inox2d"], cwd=repo_root)

    if not puppet_path.exists():
        raise FileNotFoundError(f"puppet not found: {puppet_path} (hint: pass --puppet or rerun without --skip-prepare)")

    live_dir.mkdir(parents=True, exist_ok=True)

    print("== What this does ==")
    print("- Start vtuber pipeline: bilibili danmaku -> LLM -> subtitle.txt + events.jsonl + wav (TTS)")
    print("- Start Love2D frontend: render puppet + read subtitle + play wav + drive mouth/blink/etc")
    print()

    # Love2D frontend env
    love_env = os.environ.copy()
    love_env["MORI_LIVE_DIR"] = str(live_dir)
    love_env["MORI_PUPPET_PATH"] = str(puppet_path)
    lib_path = (repo_root / "model" / "inochi2d" / "native" / "libmori_inox2d.so").resolve()
    if lib_path.is_file():
        love_env.setdefault("MORI_INOX2D_LIB", str(lib_path))
    # Distributed deployments: default disable mouse look unless explicitly enabled.
    if str(args.mouse_look or "").strip():
        love_env["MORI_MOUSE_LOOK"] = str(args.mouse_look).strip()
    else:
        love_env.setdefault("MORI_MOUSE_LOOK", "0")
    if str(args.mapping or "").strip():
        love_env["MORI_MAPPING_PATH"] = str(Path(args.mapping).expanduser().resolve())

    love_bin = _resolve_love_bin(str(args.love_bin))
    love_cmd = [love_bin, str((repo_root / "mori_live2d" / "love2d_frontend").resolve())]
    print("love> " + " ".join(love_cmd))
    love_proc = subprocess.Popen(love_cmd, cwd=str(repo_root), env=love_env, text=True)
    print(f"love> pid={love_proc.pid} live_dir={live_dir}")

    # vtuber.py cmd (always enable TTS so Love2D can play wav)
    vtuber_cmd = [sys.executable, "-u", str((repo_root / "vtuber.py").resolve())]
    vtuber_cmd += ["--workdir", str(workdir)]
    vtuber_cmd += ["--live-dir", str(args.live_dir)]
    if int(args.bilibili_room_id or 0) > 0:
        vtuber_cmd += ["--bilibili-room-id", str(int(args.bilibili_room_id))]
    if str(args.bilibili_room_url or "").strip():
        vtuber_cmd += ["--bilibili-room-url", str(args.bilibili_room_url)]
    vtuber_cmd += ["--bilibili-interval", str(float(args.bilibili_interval))]
    vtuber_cmd += ["--bilibili-catchup", str(int(args.bilibili_catchup or 0))]
    if bool(args.exit_when_offline):
        vtuber_cmd.append("--bilibili-exit-when-offline")
    vtuber_cmd.append("--tts")
    if bool(args.tts_cuda):
        vtuber_cmd.append("--tts-cuda")
    if str(args.tts_root or "").strip():
        vtuber_cmd += ["--tts-root", str(args.tts_root)]

    print("vtuber> " + " ".join(vtuber_cmd))
    vtuber_proc = subprocess.Popen(vtuber_cmd, cwd=str(repo_root), text=True)
    print(f"vtuber> pid={vtuber_proc.pid}")

    try:
        while True:
            code = vtuber_proc.poll()
            if code is not None:
                return int(code)
            if love_proc.poll() is not None:
                # If frontend dies, stop vtuber so it doesn't keep generating files forever.
                try:
                    vtuber_proc.send_signal(signal.SIGINT)
                except Exception:
                    vtuber_proc.terminate()
                return int(vtuber_proc.wait())
            time.sleep(0.2)
    except KeyboardInterrupt:
        print()
        try:
            vtuber_proc.send_signal(signal.SIGINT)
        except Exception:
            vtuber_proc.terminate()
        return int(vtuber_proc.wait())
    finally:
        if love_proc.poll() is None:
            try:
                love_proc.terminate()
            except Exception:
                pass
            for _ in range(30):
                if love_proc.poll() is not None:
                    break
                time.sleep(0.1)
            if love_proc.poll() is None:
                try:
                    love_proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
