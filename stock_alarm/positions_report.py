from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean

from .app import latest_naver_trading_day, latest_sell_alert_times, load_env, naver_rows, parse_time, stock_name, write_error_log
from .sell_check import read_positions


POSITIONS_REPORT_LOG = "logs/positions_report.csv"


@dataclass(frozen=True)
class PositionRow:
    ticker: str
    name: str
    entry_price: int
    close: int
    return_pct: float


def position_rows(positions: list[dict[str, str]], end_day: date) -> list[PositionRow]:
    rows = []
    for position in positions:
        ticker = position["ticker"].strip()
        entry_price = int(float(position["entry_price"]))
        prices = naver_rows(ticker, end_day - timedelta(days=10), end_day)
        if not prices or entry_price <= 0:
            continue
        close = int(prices[-1][4])
        name = stock_name(ticker, position.get("name", ticker).strip() or ticker)
        rows.append(PositionRow(ticker, name, entry_price, close, (close - entry_price) / entry_price * 100))
    return rows


def active_positions(positions: list[dict[str, str]], sell_alerts_path: str = "logs/sell_alerts.csv") -> list[dict[str, str]]:
    sell_alerts = latest_sell_alert_times(sell_alerts_path)
    latest: dict[str, tuple[datetime, dict[str, str]]] = {}
    for position in positions:
        ticker = position.get("ticker", "").strip()
        entry_time = parse_time(position.get("entry_date", ""))
        if not ticker or not entry_time:
            continue
        sell_time = sell_alerts.get(ticker)
        if sell_time and entry_time <= sell_time:
            continue
        if ticker not in latest or entry_time > latest[ticker][0]:
            latest[ticker] = (entry_time, position)
    return [row for _entry_time, row in latest.values()]


def summary(rows: list[PositionRow]) -> str:
    if not rows:
        return "positions=0"
    worst = min(rows, key=lambda row: row.return_pct)
    return f"positions={len(rows)} avg_return={mean(row.return_pct for row in rows):.2f}% worst={worst.name}({worst.ticker}) {worst.return_pct:.2f}%"


def lines(rows: list[PositionRow], change: str = "") -> list[str]:
    output = ["# positions report", summary(rows)]
    if change:
        output.append(change)
    output.extend(
        f"- {row.name}({row.ticker}) entry={row.entry_price:,} close={row.close:,} return={row.return_pct:.2f}%"
        for row in rows
    )
    return output


def write_log(rows: list[PositionRow], path: str = POSITIONS_REPORT_LOG) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(["created_at", "ticker", "name", "entry_price", "close", "return_pct"])
        for row in rows:
            writer.writerow(
                [
                    datetime.now().isoformat(timespec="seconds"),
                    row.ticker,
                    row.name,
                    row.entry_price,
                    row.close,
                    f"{row.return_pct:.2f}",
                ]
            )


def read_log(path: str = POSITIONS_REPORT_LOG) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def change_summary(path: str = POSITIONS_REPORT_LOG) -> str:
    snapshots: dict[str, list[float]] = {}
    for row in read_log(path):
        created_at = row.get("created_at", "")
        if created_at:
            snapshots.setdefault(created_at, []).append(float(row.get("return_pct") or 0))
    if len(snapshots) < 2:
        return "change=not enough history"
    previous, current = list(snapshots.values())[-2:]
    return f"change={mean(current) - mean(previous):+.2f}p since previous"


def run() -> list[str]:
    load_env()
    rows = position_rows(active_positions(read_positions()), latest_naver_trading_day())
    write_log(rows)
    return lines(rows, change_summary())


def main() -> None:
    try:
        print("\n".join(run()))
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
