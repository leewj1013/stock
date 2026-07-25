from __future__ import annotations

import sys
import os

from .app import save_env_value


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: python -m stock_alarm.app_setenv KEY [VALUE]")
    value = sys.argv[2] if len(sys.argv) == 3 else os.environ.get("STOCK_ALARM_SETENV_VALUE", "")
    if not value:
        raise SystemExit("missing value")
    save_env_value(sys.argv[1], value)


if __name__ == "__main__":
    main()
