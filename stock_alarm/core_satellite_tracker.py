"""Forward record of the 30% index core + 70% strategy satellite layout.

Comparison only, no orders. The core is KODEX 200 (069500) at its market
price, so the ETF fee is already inside the price. The satellite is units of
the exp_control account's equity (same rules as aggressive, 1억 funded), so
no second copy of the strategy has to trade. Rebalanced to 30/70 on the first
run of each quarter, paying MOVE_COST on the amount moved -- the same
assumptions as the 2026-09-15 core-satellite backtest.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta

from .data_store import connect, query_rows

DB_PATH = "data/stock_alarm_exp_core30.db"
SATELLITE_DB_PATH = "data/stock_alarm_exp_control.db"
CORE_TICKER = "069500"
CORE_WEIGHT = 0.3
START_CAPITAL = 100_000_000
MOVE_COST = 0.0005

SCHEMA = """CREATE TABLE IF NOT EXISTS core_satellite_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    core_price INTEGER NOT NULL,
    satellite_equity INTEGER NOT NULL,
    core_shares REAL NOT NULL,
    satellite_units REAL NOT NULL,
    core_value INTEGER NOT NULL,
    satellite_value INTEGER NOT NULL,
    equity INTEGER NOT NULL,
    return_pct REAL NOT NULL,
    rebalanced INTEGER NOT NULL
)"""


def quarter(timestamp: str) -> tuple[int, int]:
    return int(timestamp[:4]), (int(timestamp[5:7]) - 1) // 3


def next_snapshot(previous: dict | None, core_price: int, satellite_equity: int, now: str) -> dict:
    # ponytail: fractional ETF shares/units; integer shares only matter below ~1억 scale
    if previous is None:
        total, moved = START_CAPITAL, START_CAPITAL
        rebalance = True
    else:
        core_value = previous["core_shares"] * core_price
        total = core_value + previous["satellite_units"] * satellite_equity
        rebalance = quarter(now) != quarter(previous["created_at"])
        moved = abs(core_value - total * CORE_WEIGHT) if rebalance else 0
    if rebalance:
        if previous is not None:
            total -= moved * MOVE_COST
        core_shares = total * CORE_WEIGHT / core_price
        satellite_units = total * (1 - CORE_WEIGHT) / satellite_equity
    else:
        core_shares, satellite_units = previous["core_shares"], previous["satellite_units"]
    core_value = round(core_shares * core_price)
    satellite_value = round(satellite_units * satellite_equity)
    equity = core_value + satellite_value
    return {
        "created_at": now, "core_price": core_price, "satellite_equity": satellite_equity,
        "core_shares": core_shares, "satellite_units": satellite_units,
        "core_value": core_value, "satellite_value": satellite_value, "equity": equity,
        "return_pct": round((equity / START_CAPITAL - 1) * 100, 4), "rebalanced": int(rebalance),
    }


def record(core_price: int, satellite_equity: int, path: str = DB_PATH, now: str | None = None) -> dict:
    now = now or datetime.now().isoformat(timespec="seconds")
    with closing(connect(path)) as connection:
        connection.execute(SCHEMA)
        row = connection.execute("SELECT * FROM core_satellite_snapshots ORDER BY snapshot_id DESC LIMIT 1").fetchone()
        snapshot = next_snapshot(dict(row) if row else None, core_price, satellite_equity, now)
        columns = list(snapshot)
        connection.execute(
            f"INSERT INTO core_satellite_snapshots ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [snapshot[column] for column in columns],
        )
        connection.commit()
    return snapshot


def run() -> dict | None:
    from .app import naver_rows
    satellite = query_rows("SELECT equity FROM virtual_valuation_snapshots ORDER BY snapshot_id DESC LIMIT 1", path=SATELLITE_DB_PATH)
    rows = naver_rows(CORE_TICKER, date.today() - timedelta(days=10), date.today(), max_cache_age_seconds=60)
    if not satellite or not rows or int(rows[-1][4]) <= 0:
        print("core_satellite skipped missing_prices")
        return None
    snapshot = record(int(rows[-1][4]), int(satellite[0]["equity"]))
    print(f"core_satellite[core30] equity={snapshot['equity']:,} return={snapshot['return_pct']:.2f}% rebalanced={snapshot['rebalanced']}")
    return snapshot


if __name__ == "__main__":
    run()
