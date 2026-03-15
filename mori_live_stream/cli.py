from __future__ import annotations

import argparse
import sys

from .bilibili_live import BilibiliLivePoller, iter_poll


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="mori-live-stream", description="Minimal Bilibili live danmaku poller.")
    p.add_argument("--room-id", type=int, required=True, help="Bilibili live room id.")
    p.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds.")
    p.add_argument("--stop-after", type=float, default=0.0, help="Stop after seconds (0 = run forever).")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    poller = BilibiliLivePoller(room_id=int(args.room_id))
    try:
        for msg in iter_poll(poller, interval_s=float(args.interval), stop_after_s=float(args.stop_after)):
            sys.stdout.write(f"[{msg.timeline}] {msg.nickname}: {msg.text}\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

