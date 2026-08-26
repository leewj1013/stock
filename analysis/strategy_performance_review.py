"""Reproduce the recommendation and sell-signal performance review."""

from __future__ import annotations

import csv
from math import sqrt
from statistics import mean, median, stdev


def read_rows(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def values(rows: list[dict[str, str]], column: str) -> list[float]:
    return [float(row[column]) for row in rows if row.get(column)]


def summary(items: list[float], favorable_positive: bool = True) -> dict[str, float | int]:
    average = mean(items)
    standard_error = stdev(items) / sqrt(len(items)) if len(items) > 1 else 0
    favorable = sum(item > 0 if favorable_positive else item < 0 for item in items)
    return {
        "n": len(items),
        "mean": round(average, 2),
        "median": round(median(items), 2),
        "favorable_rate_pct": round(favorable / len(items) * 100, 1),
        "ci95_low": round(average - 1.96 * standard_error, 2),
        "ci95_high": round(average + 1.96 * standard_error, 2),
    }


def unique_sell_episodes(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result = []
    for row in reversed(rows):
        key = (row.get("ticker", ""), row.get("entry_price", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def main() -> None:
    recommendations = read_rows("logs/recommendation_performance.csv")
    sells = read_rows("logs/sell_performance.csv")
    sell_episodes = unique_sell_episodes(sells)
    print(f"recommendation_rows={len(recommendations)}")
    for horizon in (1, 3, 5):
        print(f"recommendation_{horizon}d={summary(values(recommendations, f'return_{horizon}d_pct'))}")
    print(f"sell_rows={len(sells)} unique_episode_rows={len(sell_episodes)}")
    for horizon in (1, 3, 5):
        print(f"sell_{horizon}d={summary(values(sell_episodes, f'return_{horizon}d_pct'), False)}")


if __name__ == "__main__":
    main()
