from __future__ import annotations

from mori_runtime.entry import run_cli


def main() -> int:
    return int(run_cli() or 0)


if __name__ == "__main__":
    raise SystemExit(main())

