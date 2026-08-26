from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Callable


def ensure_header(path: str, expected: list[str], migrate: Callable[[list[str], list[str]], list[str]] | None = None) -> None:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, newline="", encoding="utf-8-sig") as file:
        rows = list(csv.reader(file))
    if not rows or rows[0] == expected:
        return
    old_header = rows[0]
    converted = []
    for row in rows[1:]:
        if migrate:
            converted.append(migrate(old_header, row))
            continue
        values = {name: row[index] for index, name in enumerate(old_header) if index < len(row)}
        converted.append([values.get(name, "") for name in expected])
    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(prefix="csv-migrate-", suffix=".csv", dir=directory)
    os.close(descriptor)
    try:
        with open(temporary, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(expected)
            writer.writerows(converted)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate_recommendation_row(old_header: list[str], row: list[str]) -> list[str]:
    expected = ["created_at", "ticker", "name", "close", "volume_ratio", "trading_value", "score", "volume_score", "trading_value_score", "trend_score", "news_score", "disclosure_score", "performance_penalty", "raw_volume_ratio", "expected_volume_fraction", "atr20_pct", "relative_strength_pct", "relative_strength_score", "financial_score", "raw_trading_value", "allocation_pct"]
    if old_header == expected[:7]:
        return (row + [""] * len(expected))[: len(expected)]
    values = {name: row[index] for index, name in enumerate(old_header) if index < len(row)}
    return [values.get(name, "") for name in expected]


def migrate_sell_alert_row(old_header: list[str], row: list[str]) -> list[str]:
    expected = ["created_at", "ticker", "name", "entry_price", "close", "return_pct", "summary", "reason"]
    if old_header == ["created_at", "ticker", "name", "entry_price", "close", "return_pct", "reason"]:
        if len(row) >= 8:
            return row[:8]
        padded = row + [""] * (7 - len(row))
        return [*padded[:6], "", padded[6]]
    values = {name: row[index] for index, name in enumerate(old_header) if index < len(row)}
    return [values.get(name, "") for name in expected]
