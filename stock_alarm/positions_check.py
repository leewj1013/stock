from __future__ import annotations

import csv
import os
import re

from .app import POSITIONS_PATH
from .sell_check import read_positions


def validate_positions(path: str = POSITIONS_PATH) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for line_no, row in enumerate(read_positions(path), start=2):
        ticker = (row.get("ticker") or "").strip()
        name = (row.get("name") or "").strip()
        entry_price = (row.get("entry_price") or "").strip()
        if not re.fullmatch(r"\d{6}", ticker):
            errors.append(f"line {line_no}: invalid ticker {ticker!r}")
        if not name:
            errors.append(f"line {line_no}: empty name")
        try:
            if float(entry_price) <= 0:
                errors.append(f"line {line_no}: entry_price must be positive")
        except ValueError:
            errors.append(f"line {line_no}: invalid entry_price {entry_price!r}")
        if ticker in seen:
            errors.append(f"line {line_no}: duplicate ticker {ticker}")
        seen.add(ticker)
    return errors


def position_count(path: str = POSITIONS_PATH) -> int:
    return len(read_positions(path))


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
