from __future__ import annotations

from mori_runtime.entry import run_vtuber


def main() -> int:
    return int(run_vtuber() or 0)


if __name__ == "__main__":
    raise SystemExit(main())

