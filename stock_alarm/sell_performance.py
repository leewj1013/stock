from __future__ import annotations

import csv
import hashlib
import os
from datetime import datetime

from .app import load_env, write_error_log
from .backtest import naver_close_after
from .data_store import write_sell_outcomes
from .recommendation_performance import next_execution


ALERT_PATH = "logs/sell_alerts.csv"
OUT_PATH = "logs/sell_performance.csv"
HOLD_DAYS = (1, 3, 5, 10)


def outcome_rows(path: str = ALERT_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    cost_bps = int(os.environ.get("EXECUTION_COST_BPS", "30"))
    cost_pct = cost_bps / 100
    results: list[dict] = []
    seen_episodes: set[tuple[str, str]] = set()
    with open(path, newline="", encoding="utf-8-sig") as file:
        alerts = list(csv.DictReader(file))
    for alert in alerts:
        created_at = alert.get("created_at", "")
        ticker = alert.get("ticker", "")
        if not created_at or not ticker:
            continue
        alert_day = datetime.fromisoformat(created_at).date()
        execution = next_execution(ticker, alert_day)
        execution_day, price = execution if execution else (None, None)
        execution_date = execution_day.isoformat() if execution_day else ""
        episode = (ticker, alert.get("entry_price", ""))
        if episode in seen_episodes:
            continue
        seen_episodes.add(episode)
        key = hashlib.sha256("|".join(episode).encode()).hexdigest()[:24]
        row = {"alert_key": key, "alert_created_at": created_at, "ticker": ticker, "name": alert.get("name", ""), "entry_price": int(float(alert.get("entry_price") or 0)), "alert_close": int(float(alert.get("close") or 0)), "execution_date": execution_date or None, "execution_price": price, "execution_cost_bps": cost_bps, "updated_at": datetime.now().isoformat(timespec="seconds")}
        for days in HOLD_DAYS:
            close = naver_close_after(ticker, execution_day, days) if execution_day and price else None
            row[f"return_{days}d_pct"] = None if close is None else (close - price) / price * 100 - cost_pct
        results.append(row)
    return results


def write_csv(rows: list[dict], path: str = OUT_PATH) -> None:
    columns = ["alert_key", "alert_created_at", "ticker", "name", "entry_price", "alert_close", "execution_date", "execution_price", "return_1d_pct", "return_3d_pct", "return_5d_pct", "return_10d_pct", "execution_cost_bps", "updated_at"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run() -> int:
    load_env()
    rows = outcome_rows()
    write_csv(rows)
    write_sell_outcomes(rows)
    return len(rows)


def main() -> None:
    try:
        print(f"sell_outcomes={run()}")
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
