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


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mori_runtime.config import DEFAULT_CONFIG_NAME, apply_config_defaults  # noqa: E402


def _default_puppet_path(repo_root: Path) -> Path:
    return (repo_root / "model" / "inochi2d" / "puppets" / "aka" / "Aka.inx").resolve()


def _resolve_live_dir(*, workdir: Path, live_dir: str) -> Path:
    p = Path(live_dir).expanduser()
    return (p if p.is_absolute() else (workdir / p)).resolve()


def _clear_session_artifacts(*, live_dir: Path, subtitle_file: str, event_log: str) -> None:
    live_dir.mkdir(parents=True, exist_ok=True)

    subtitle_name = str(subtitle_file or "").strip()
    event_name = str(event_log or "").strip()
    clear_targets: list[Path] = []
    if subtitle_name:
        clear_targets.append((live_dir / subtitle_name).resolve())
    if event_name:
        clear_targets.append((live_dir / event_name).resolve())
    clear_targets.append((live_dir / "mouth.txt").resolve())

    for path in clear_targets:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        except Exception:
            pass

    audio_dir = (live_dir / "audio").resolve()
    try:
        audio_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    for path in audio_dir.glob("turn_*.wav"):
        try:
            path.unlink()
        except Exception:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = _repo_root()
    p = argparse.ArgumentParser(
        prog="run-bili-vtuber-love2d",
        description="Launch: Love2D frontend (Inox2D) + vtuber.py (bilibili danmaku -> subtitles + TTS wav playback).",
    )
    p.add_argument(
        "--config",
        default="",
        help=f"Path to unified config JSON (auto-loads ./{DEFAULT_CONFIG_NAME} or <repo>/{DEFAULT_CONFIG_NAME} when present).",
    )
    p.add_argument("--workdir", default=str(repo_root), help="Passed to vtuber.py --workdir.")
    p.add_argument("--live-dir", default="live", help="Passed to vtuber.py --live-dir (relative to --workdir if not absolute).")
    p.add_argument("--subtitle-file", default="subtitle.txt", help="Passed to vtuber.py --subtitle-file (empty = disable subtitle file).")
    p.add_argument("--event-log", default="events.jsonl", help="Passed to vtuber.py --event-log.")
    p.add_argument("--print-to-stdout", action="store_true", help="Passed to vtuber.py --print-to-stdout.")

    p.add_argument("--bilibili-room-url", default="", help="Passed to vtuber.py --bilibili-room-url.")
    p.add_argument("--bilibili-room-id", type=int, default=0, help="Passed to vtuber.py --bilibili-room-id.")
    p.add_argument("--bilibili-interval", type=float, default=2.0, help="Passed to vtuber.py --bilibili-interval.")
    p.add_argument("--bilibili-catchup", type=int, default=0, help="Passed to vtuber.py --bilibili-catchup.")
    p.add_argument("--exit-when-offline", action="store_true", help="Passed to vtuber.py --bilibili-exit-when-offline.")

    p.add_argument("--tts", action="store_true", help="Enable TTS (passed to vtuber.py --tts).")
    p.add_argument(
        "--tts-backend",
        choices=["lux", "zipvoice"],
        default="",
        help="Optional override for vtuber.py --tts-backend.",
    )
    p.add_argument("--tts-model", default="", help="Optional override for vtuber.py --tts-model (backend=lux).")
    p.add_argument("--tts-device", choices=["auto", "cpu", "cuda", "mps", "metal"], default="auto", help="Passed to vtuber.py --tts-device (backend=lux).")
    p.add_argument("--tts-threads", type=int, default=0, help="Optional override for vtuber.py --tts-threads (backend=lux).")
    p.add_argument("--tts-prompt-wav", default="", help="Passed to vtuber.py --tts-prompt-wav (required).")
    p.add_argument("--tts-prompt-duration", type=float, default=0.0, help="Optional override for vtuber.py --tts-prompt-duration (backend=lux).")
    p.add_argument("--tts-prompt-rms", type=float, default=0.0, help="Optional override for vtuber.py --tts-prompt-rms (backend=lux).")
    p.add_argument("--tts-num-steps", type=int, default=0, help="Optional override for vtuber.py --tts-num-steps.")
    p.add_argument("--tts-guidance-scale", type=float, default=0.0, help="Optional override for vtuber.py --tts-guidance-scale.")
    p.add_argument("--tts-t-shift", type=float, default=0.0, help="Optional override for vtuber.py --tts-t-shift.")
    p.add_argument("--tts-speed", type=float, default=0.0, help="Optional override for vtuber.py --tts-speed.")
    p.add_argument("--tts-return-smooth", action="store_true", help="Passed to vtuber.py --tts-return-smooth (backend=lux).")
    p.add_argument("--tts-zipvoice-python-bin", default="", help="Optional override for vtuber.py --tts-zipvoice-python-bin.")
    p.add_argument("--tts-zipvoice-repo", default="", help="Optional override for vtuber.py --tts-zipvoice-repo.")
    p.add_argument("--tts-zipvoice-model-dir", default="", help="Optional override for vtuber.py --tts-zipvoice-model-dir.")
    p.add_argument("--tts-zipvoice-checkpoint-name", default="", help="Optional override for vtuber.py --tts-zipvoice-checkpoint-name.")
    p.add_argument("--tts-zipvoice-zh-prompt-text", default="", help="Optional override for vtuber.py --tts-zipvoice-zh-prompt-text.")
    p.add_argument("--tts-zipvoice-ja-prompt-text", default="", help="Optional override for vtuber.py --tts-zipvoice-ja-prompt-text.")
    p.add_argument("--tts-zipvoice-zh-tokenizer", default="", help="Optional override for vtuber.py --tts-zipvoice-zh-tokenizer.")
    p.add_argument("--tts-zipvoice-zh-lang", default="", help="Optional override for vtuber.py --tts-zipvoice-zh-lang.")
    p.add_argument("--tts-zipvoice-ja-tokenizer", default="", help="Optional override for vtuber.py --tts-zipvoice-ja-tokenizer.")
    p.add_argument("--tts-zipvoice-ja-lang", default="", help="Optional override for vtuber.py --tts-zipvoice-ja-lang.")
    p.add_argument("--tts-zipvoice-remove-long-sil", action="store_true", help="Passed to vtuber.py --tts-zipvoice-remove-long-sil.")
    p.add_argument("--tts-zipvoice-num-thread", type=int, default=0, help="Optional override for vtuber.py --tts-zipvoice-num-thread.")

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
    apply_config_defaults(p, argv=argv, repo_root=repo_root, profile="love2d")
    return p.parse_args(argv)


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
    _clear_session_artifacts(
        live_dir=live_dir,
        subtitle_file=str(args.subtitle_file or ""),
        event_log=str(args.event_log or ""),
    )

    print("== What this does ==")
    print("- Start vtuber pipeline: bilibili danmaku -> LLM -> subtitle.txt + events.jsonl + optional wav (TTS)")
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

    # vtuber.py cmd
    vtuber_cmd = [sys.executable, "-u", str((repo_root / "vtuber.py").resolve())]
    if str(args.config or "").strip():
        vtuber_cmd += ["--config", str(args.config)]
    vtuber_cmd += ["--workdir", str(workdir)]
    vtuber_cmd += ["--live-dir", str(args.live_dir)]
    vtuber_cmd += ["--subtitle-file", str(args.subtitle_file)]
    vtuber_cmd += ["--event-log", str(args.event_log)]
    if bool(args.print_to_stdout):
        vtuber_cmd.append("--print-to-stdout")
    if int(args.bilibili_room_id or 0) > 0:
        vtuber_cmd += ["--bilibili-room-id", str(int(args.bilibili_room_id))]
    if str(args.bilibili_room_url or "").strip():
        vtuber_cmd += ["--bilibili-room-url", str(args.bilibili_room_url)]
    vtuber_cmd += ["--bilibili-interval", str(float(args.bilibili_interval))]
    vtuber_cmd += ["--bilibili-catchup", str(int(args.bilibili_catchup or 0))]
    if bool(args.exit_when_offline):
        vtuber_cmd.append("--bilibili-exit-when-offline")
    if bool(args.tts):
        vtuber_cmd.append("--tts")
        tts_backend = str(getattr(args, "tts_backend", "") or "").strip()
        if tts_backend:
            vtuber_cmd += ["--tts-backend", tts_backend]
        if str(args.tts_model or "").strip():
            vtuber_cmd += ["--tts-model", str(args.tts_model)]
        if str(args.tts_device or "").strip():
            vtuber_cmd += ["--tts-device", str(args.tts_device)]
        if int(args.tts_threads or 0) > 0:
            vtuber_cmd += ["--tts-threads", str(int(args.tts_threads))]

        prompt_wav = str(args.tts_prompt_wav or "").strip()
        if not prompt_wav:
            raise ValueError("Missing required arg: --tts-prompt-wav")
        vtuber_cmd += ["--tts-prompt-wav", str(Path(prompt_wav).expanduser().resolve())]
        if float(args.tts_prompt_duration or 0.0) > 0.0:
            vtuber_cmd += ["--tts-prompt-duration", str(float(args.tts_prompt_duration))]
        if float(args.tts_prompt_rms or 0.0) > 0.0:
            vtuber_cmd += ["--tts-prompt-rms", str(float(args.tts_prompt_rms))]
        if int(args.tts_num_steps or 0) > 0:
            vtuber_cmd += ["--tts-num-steps", str(int(args.tts_num_steps))]
        if float(args.tts_guidance_scale or 0.0) > 0.0:
            vtuber_cmd += ["--tts-guidance-scale", str(float(args.tts_guidance_scale))]
        if float(args.tts_t_shift or 0.0) > 0.0:
            vtuber_cmd += ["--tts-t-shift", str(float(args.tts_t_shift))]
        if float(args.tts_speed or 0.0) > 0.0:
            vtuber_cmd += ["--tts-speed", str(float(args.tts_speed))]
        if bool(args.tts_return_smooth):
            vtuber_cmd.append("--tts-return-smooth")
        if str(args.tts_zipvoice_python_bin or "").strip():
            vtuber_cmd += ["--tts-zipvoice-python-bin", str(Path(args.tts_zipvoice_python_bin).expanduser().resolve())]
        if str(args.tts_zipvoice_repo or "").strip():
            vtuber_cmd += ["--tts-zipvoice-repo", str(Path(args.tts_zipvoice_repo).expanduser().resolve())]
        if str(args.tts_zipvoice_model_dir or "").strip():
            vtuber_cmd += ["--tts-zipvoice-model-dir", str(Path(args.tts_zipvoice_model_dir).expanduser().resolve())]
        if str(args.tts_zipvoice_checkpoint_name or "").strip():
            vtuber_cmd += ["--tts-zipvoice-checkpoint-name", str(args.tts_zipvoice_checkpoint_name)]
        if str(args.tts_zipvoice_zh_prompt_text or "").strip():
            vtuber_cmd += ["--tts-zipvoice-zh-prompt-text", str(args.tts_zipvoice_zh_prompt_text)]
        if str(args.tts_zipvoice_ja_prompt_text or "").strip():
            vtuber_cmd += ["--tts-zipvoice-ja-prompt-text", str(args.tts_zipvoice_ja_prompt_text)]
        if str(args.tts_zipvoice_zh_tokenizer or "").strip():
            vtuber_cmd += ["--tts-zipvoice-zh-tokenizer", str(args.tts_zipvoice_zh_tokenizer)]
        if str(args.tts_zipvoice_zh_lang or "").strip():
            vtuber_cmd += ["--tts-zipvoice-zh-lang", str(args.tts_zipvoice_zh_lang)]
        if str(args.tts_zipvoice_ja_tokenizer or "").strip():
            vtuber_cmd += ["--tts-zipvoice-ja-tokenizer", str(args.tts_zipvoice_ja_tokenizer)]
        if str(args.tts_zipvoice_ja_lang or "").strip():
            vtuber_cmd += ["--tts-zipvoice-ja-lang", str(args.tts_zipvoice_ja_lang)]
        if bool(args.tts_zipvoice_remove_long_sil):
            vtuber_cmd.append("--tts-zipvoice-remove-long-sil")
        if int(args.tts_zipvoice_num_thread or 0) > 0:
            vtuber_cmd += ["--tts-zipvoice-num-thread", str(int(args.tts_zipvoice_num_thread))]

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
