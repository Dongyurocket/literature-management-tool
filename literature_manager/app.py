from __future__ import annotations

from .qt_app import main as qt_main


def main() -> int:
    return qt_main()


if __name__ == "__main__":
    raise SystemExit(main())
