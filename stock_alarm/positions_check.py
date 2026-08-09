from __future__ import annotations

import csv
import os
import re
from datetime import date
from math import isfinite

from .app import POSITIONS_PATH
from .sell_check import read_positions


def validate_positions(path: str = POSITIONS_PATH) -> list[str]:
    errors: list[str] = []
    seen_positions: set[tuple[str, str, float]] = set()
    for line_no, row in enumerate(read_positions(path), start=2):
        ticker = (row.get("ticker") or "").strip()
        name = (row.get("name") or "").strip()
        entry_price = (row.get("entry_price") or "").strip()
        entry_date = (row.get("entry_date") or "").strip()
        if not re.fullmatch(r"\d{6}", ticker):
            errors.append(f"line {line_no}: invalid ticker {ticker!r}")
        if not name:
            errors.append(f"line {line_no}: empty name")
        parsed_price: float | None = None
        try:
            parsed_price = float(entry_price)
            if not isfinite(parsed_price) or parsed_price <= 0:
                errors.append(f"line {line_no}: entry_price must be positive")
        except ValueError:
            errors.append(f"line {line_no}: invalid entry_price {entry_price!r}")
        try:
            date.fromisoformat(entry_date)
        except ValueError:
            errors.append(f"line {line_no}: invalid entry_date {entry_date!r}")
        if re.fullmatch(r"\d{6}", ticker) and parsed_price is not None and isfinite(parsed_price) and parsed_price > 0 and entry_date:
            key = (ticker, entry_date, parsed_price)
            if key in seen_positions:
                errors.append(f"line {line_no}: duplicate position {ticker} {entry_date} {parsed_price:g}")
            seen_positions.add(key)
    return errors


def position_count(path: str = POSITIONS_PATH) -> int:
    return len(read_positions(path))


def active_position_tickers(path: str = POSITIONS_PATH, sell_alerts_path: str = "logs/sell_alerts.csv") -> set[str]:
    from .app import active_position_tickers as app_active_position_tickers

    return app_active_position_tickers(path, sell_alerts_path)


def active_position_count(path: str = POSITIONS_PATH, sell_alerts_path: str = "logs/sell_alerts.csv") -> int:
    return len(active_position_tickers(path, sell_alerts_path))


def main() -> int:
    errors = validate_positions()
    if errors:
        print("positions ok=False")
        print(os.linesep.join(errors))
        return 1
    print(f"positions ok=True count={position_count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
