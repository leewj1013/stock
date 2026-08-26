from __future__ import annotations

import sys
from datetime import datetime, time

from .app import is_trading_day, load_env


def should_run(mode: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if mode in {"intraday", "sell"}:
        if now.weekday() >= 5:
            return False
        if not time(8, 50) <= now.time() <= time(15, 40):
            return False
        # The 08:50 preparation run occurs before Naver publishes today's first row.
        return True if now.time() < time(9, 0) else is_trading_day(now.date())
    if mode == "open":
        return now.weekday() < 5
    if mode in {"daily", "issue_alert"}:
        if now.weekday() >= 5:
            return False
        return is_trading_day(now.date())
    return True


def main() -> int:
    load_env()
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if should_run(mode):
        print(f"run {mode}")
        return 0
    print(f"skip {mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
