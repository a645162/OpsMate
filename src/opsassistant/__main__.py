"""允许 ``python -m opsassistant``。"""

from opsassistant.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
