from __future__ import annotations

import csv
import os
import re

from stock_alarm.app import WATCHLIST_PATH


def validate_watchlist(path: str = WATCHLIST_PATH) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()

    with open(path, newline="", encoding="utf-8") as file:
        for line_no, row in enumerate(csv.DictReader(file), start=2):
            ticker = (row.get("ticker") or "").strip()
            name = (row.get("name") or "").strip()
            if not re.fullmatch(r"\d{6}", ticker):
                errors.append(f"line {line_no}: invalid ticker {ticker!r}")
            if not name:
                errors.append(f"line {line_no}: empty name")
            if ticker in seen:
                errors.append(f"line {line_no}: duplicate ticker {ticker}")
            seen.add(ticker)

    if not seen:
        errors.append("watchlist is empty")
    return errors


def main() -> int:
    errors = validate_watchlist()
    if errors:
        print("watchlist ok=False")
        print(os.linesep.join(errors))
        return 1
    with open(WATCHLIST_PATH, newline="", encoding="utf-8") as file:
        count = sum(1 for _ in csv.DictReader(file))
    print(f"watchlist ok=True count={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
