from __future__ import annotations

import csv
import os

from .app import write_error_log


def read_rows(path: str = "logs/tuning.csv") -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as file:
        return [row for row in csv.DictReader(file) if any(row.values())]


def best_row(rows: list[dict[str, str]]) -> dict[str, str] | None:
    usable = [row for row in rows if int(row.get("picks") or 0) > 0 and row.get("avg_return_pct")]
    if not usable:
        return None
    return max(usable, key=lambda row: (float(row["avg_return_pct"]), float(row.get("win_rate_pct") or 0), int(row["picks"])))


def confidence(row: dict[str, str] | None) -> str:
    if not row:
        return "none"
    picks = int(row.get("picks") or 0)
    return "weak" if picks < 20 else "ok"


def lines(path: str = "logs/tuning.csv") -> list[str]:
    rows = read_rows(path)
    best = best_row(rows)
    output = ["# tuning recommendation", f"rows={len(rows)}", f"confidence={confidence(best)}"]
    if not best:
        output.append("recommendation=run tuning first")
        return output
    output.extend(
        [
            f"VOLUME_MULTIPLIER={best['volume_multiplier']}",
            f"BACKTEST_HOLD_DAYS={best['hold_days']}",
            f"picks={best['picks']}",
            f"avg_return_pct={best['avg_return_pct']}",
            f"win_rate_pct={best['win_rate_pct']}",
        ]
    )
    return output


def main() -> None:
    try:
        print("\n".join(lines()))
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
