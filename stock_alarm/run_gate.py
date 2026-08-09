from __future__ import annotations

import sys
from datetime import datetime

from .app import is_market_alert_time, is_trading_day, load_env


def should_run(mode: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if mode == "intraday":
        if now.weekday() >= 5:
            return False
        return is_market_alert_time(now)
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
