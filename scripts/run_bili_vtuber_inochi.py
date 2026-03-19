from __future__ import annotations

import argparse
import os
import re
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


def _parse_semver(tag: str) -> tuple[int, int, int, int]:
    s = str(tag or "").strip()
    s = s[1:] if s.startswith("v") else s
    m = re.match(r"^(\\d+)(?:\\.(\\d+))?(?:\\.(\\d+))?(?:\\.(\\d+))?", s)
    if not m:
        return (0, 0, 0, 0)
    parts = [int(x) if x is not None else 0 for x in m.groups()]
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def _default_inochi_root(repo_root: Path) -> Path:
    return (repo_root / "model" / "inochi2d").resolve()


def _find_latest_inochi_session_bin(*, inochi_root: Path, platform: str) -> Path:
    base = (inochi_root / "apps" / "inochi-session").resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"Inochi Session not installed (missing dir): {base}")

    expected_name = "inochi-session.exe" if platform == "win32" else "inochi-session"
    candidates: list[tuple[tuple[int, int, int, int], float, Path]] = []
    for tag_dir in base.iterdir():
        if not tag_dir.is_dir():
            continue
        bin_path = (tag_dir / platform / expected_name).resolve()
        if bin_path.exists():
            try:
                mtime = bin_path.stat().st_mtime
            except Exception:
                mtime = 0.0
            candidates.append((_parse_semver(tag_dir.name), mtime, bin_path))

    if not candidates:
        raise FileNotFoundError(f"No Inochi Session binary found under: {base} (platform={platform})")

    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[-1][2]


def _detect_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform in ("win32", "cygwin"):
        return "win32"
    if sys.platform == "darwin":
        return "osx"
    return sys.platform


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = _repo_root()
    p = argparse.ArgumentParser(
        prog="run-bili-vtuber-inochi",
        description="Launch: Inochi Session (frontend) + vtuber.py (bilibili danmaku -> subtitles + optional TTS wav).",
    )
    p.add_argument(
        "--config",
        default="",
        help=f"Path to unified config JSON (auto-loads ./{DEFAULT_CONFIG_NAME} or <repo>/{DEFAULT_CONFIG_NAME} when present).",
    )
    p.add_argument("--workdir", default=str(repo_root), help="Passed to vtuber.py --workdir.")
    p.add_argument("--live-dir", default="live", help="Passed to vtuber.py --live-dir.")

    p.add_argument("--bilibili-room-url", default="", help="Passed to vtuber.py --bilibili-room-url.")
    p.add_argument("--bilibili-room-id", type=int, default=0, help="Passed to vtuber.py --bilibili-room-id.")
    p.add_argument("--bilibili-interval", type=float, default=2.0, help="Passed to vtuber.py --bilibili-interval.")
    p.add_argument("--bilibili-catchup", type=int, default=0, help="Passed to vtuber.py --bilibili-catchup.")
    p.add_argument(
        "--exit-when-offline",
        action="store_true",
        help="Passed to vtuber.py --bilibili-exit-when-offline.",
    )

    p.add_argument("--tts", action="store_true", help="Enable TTS (vtuber.py --tts).")
    p.add_argument("--tts-model", default="", help="Optional override for vtuber.py --tts-model.")
    p.add_argument("--tts-device", choices=["auto", "cpu", "cuda", "mps", "metal"], default="auto", help="Passed to vtuber.py --tts-device.")
    p.add_argument("--tts-threads", type=int, default=0, help="Optional override for vtuber.py --tts-threads.")
    p.add_argument("--tts-prompt-wav", default="", help="Passed to vtuber.py --tts-prompt-wav (required when --tts).")
    p.add_argument("--tts-prompt-duration", type=float, default=0.0, help="Optional override for vtuber.py --tts-prompt-duration.")
    p.add_argument("--tts-prompt-rms", type=float, default=0.0, help="Optional override for vtuber.py --tts-prompt-rms.")
    p.add_argument("--tts-num-steps", type=int, default=0, help="Optional override for vtuber.py --tts-num-steps.")
    p.add_argument("--tts-guidance-scale", type=float, default=0.0, help="Optional override for vtuber.py --tts-guidance-scale.")
    p.add_argument("--tts-t-shift", type=float, default=0.0, help="Optional override for vtuber.py --tts-t-shift.")
    p.add_argument("--tts-speed", type=float, default=0.0, help="Optional override for vtuber.py --tts-speed.")
    p.add_argument("--tts-return-smooth", action="store_true", help="Passed to vtuber.py --tts-return-smooth.")

    p.add_argument("--inochi-root", default=str(_default_inochi_root(repo_root)), help="Root for model/inochi2d.")
    p.add_argument("--inochi-bin", default="", help="Path to inochi-session executable (auto-detect if empty).")
    p.add_argument(
        "--inochi-x11",
        action="store_true",
        help="Wayland workaround: run Inochi Session with SDL_VIDEODRIVER=x11.",
    )
    apply_config_defaults(p, argv=argv, repo_root=repo_root, profile="inochi")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args()
    repo_root = _repo_root()

    print("== What this does ==")
    print("- Start vtuber pipeline: bilibili danmaku -> LLM -> subtitle.txt + events.jsonl + optional wav")
    print("- Start Inochi Session (official frontend) in parallel")
    print("== What is NOT implemented yet ==")
    print("- Programmatic puppet control (mouth/expressions) from Mori")
    print("- Sending messages back to bilibili chat (intentionally disabled)")
    print()

    platform = _detect_platform()
    inochi_root = Path(args.inochi_root).expanduser().resolve()
    inochi_bin = Path(args.inochi_bin).expanduser().resolve() if str(args.inochi_bin).strip() else None
    if inochi_bin is None:
        inochi_bin = _find_latest_inochi_session_bin(inochi_root=inochi_root, platform=platform)

    inochi_proc: subprocess.Popen[str] | None = None
    try:
        if inochi_bin.exists():
            env = None
            if bool(args.inochi_x11) and platform == "linux":
                env = {"SDL_VIDEODRIVER": "x11"}
            try:
                from mori_live2d.inochi_session import run_inochi_session

                inochi_proc = run_inochi_session(bin_path=inochi_bin, extra_env=env)
            except Exception:
                # Fallback: run directly (best-effort).
                full_env = os.environ.copy()
                if env:
                    full_env.update(env)
                inochi_proc = subprocess.Popen([str(inochi_bin)], env=full_env, text=True)
            print(f"inochi> pid={inochi_proc.pid} bin={inochi_bin}")
        else:
            print(f"inochi> not found: {inochi_bin} (skip launching)")

        vtuber_cmd = [sys.executable, "-u", str((repo_root / "vtuber.py").resolve())]
        if str(args.config or "").strip():
            vtuber_cmd += ["--config", str(args.config)]
        vtuber_cmd += ["--workdir", str(Path(args.workdir).expanduser().resolve())]
        vtuber_cmd += ["--live-dir", str(args.live_dir)]
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
        if str(args.tts_model or "").strip():
            vtuber_cmd += ["--tts-model", str(args.tts_model)]
        if bool(args.tts):
            vtuber_cmd += ["--tts-device", str(args.tts_device)]
            if int(args.tts_threads or 0) > 0:
                vtuber_cmd += ["--tts-threads", str(int(args.tts_threads))]

            prompt_wav = str(args.tts_prompt_wav or "").strip()
            if not prompt_wav:
                raise ValueError("Missing required arg for --tts: --tts-prompt-wav")
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

        print("vtuber> " + " ".join(vtuber_cmd))
        vtuber_proc = subprocess.Popen(vtuber_cmd, cwd=str(repo_root), text=True)

        try:
            return int(vtuber_proc.wait())
        except KeyboardInterrupt:
            print()
            try:
                vtuber_proc.send_signal(signal.SIGINT)
            except Exception:
                vtuber_proc.terminate()
            return int(vtuber_proc.wait())
    finally:
        if inochi_proc and inochi_proc.poll() is None:
            try:
                inochi_proc.terminate()
            except Exception:
                pass
            for _ in range(30):
                if inochi_proc.poll() is not None:
                    break
                time.sleep(0.1)
            if inochi_proc.poll() is None:
                try:
                    inochi_proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
