"""Daily investor net-buy history (foreign / institution / individual) per
ticker from Naver's mobile trend API, into the point-in-time store.

Quantities and closes are NOT split-adjusted -- use net quantity / volume
from the same row, never across rows. A day's flow is final after close, so
it may only feed decisions from the next session on.

Resumable: each ticker continues from the oldest date already stored.
    python -m stock_alarm.investor_flow_collect [start YYYYMMDD]
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from datetime import date

from .point_in_time_store import DEFAULT_PATH, connect

URL = "https://m.stock.naver.com/api/stock/{ticker}/trend?pageSize=60&bizdate={bizdate}"
WATCHLIST = "data/backtest_expanded/experimental_watchlist.csv"
SCHEMA = """CREATE TABLE IF NOT EXISTS investor_flow(
    ticker TEXT NOT NULL, bizdate TEXT NOT NULL,
    foreign_net INTEGER, organ_net INTEGER, individual_net INTEGER,
    foreign_hold_pct REAL, close INTEGER, volume INTEGER,
    PRIMARY KEY (ticker, bizdate))"""


def _num(text, cast=int):
    # Naver prints "-" (not 0) for a missing value, e.g. today's unfinished row.
    cleaned = str(text or "").replace(",", "").replace("+", "").rstrip("%")
    return None if cleaned in ("", "-") else cast(cleaned)


def parse(item: dict) -> tuple:
    return (
        item["itemCode"], item["bizdate"],
        _num(item["foreignerPureBuyQuant"]), _num(item["organPureBuyQuant"]), _num(item["individualPureBuyQuant"]),
        _num(item.get("foreignerHoldRatio"), float),
        _num(item["closePrice"]), _num(item["accumulatedTradingVolume"]),
    )


def fetch_page(ticker: str, bizdate: str) -> list[dict]:
    request = urllib.request.Request(URL.format(ticker=ticker, bizdate=bizdate), headers={"User-Agent": "Mozilla/5.0 stockAlarm investor-flow"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_ticker(connection, ticker: str, start: str, pause: float = 0.3) -> int:
    oldest, newest = connection.execute("SELECT MIN(bizdate), MAX(bizdate) FROM investor_flow WHERE ticker=?", (ticker,)).fetchone()
    # bizdate is exclusive: the API returns sessions strictly before it, so
    # starting at today skips today's still-changing row. Fill forward from
    # today down to what is stored, then keep backfilling below the oldest.
    today = date.today().strftime("%Y%m%d")
    added = _walk_back(connection, ticker, today, newest or start, pause)
    if oldest:
        added += _walk_back(connection, ticker, oldest, start, pause)
    return added


def _walk_back(connection, ticker: str, cursor: str, stop: str, pause: float) -> int:
    added = 0
    while cursor > stop:
        for attempt in range(3):
            try:
                items = fetch_page(ticker, cursor)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
        rows = [parse(item) for item in items]
        if not rows:
            break  # before listing
        connection.executemany("INSERT OR IGNORE INTO investor_flow VALUES (?,?,?,?,?,?,?,?)", rows)
        connection.commit()
        added += len(rows)
        cursor = min(row[1] for row in rows)
        time.sleep(pause)
    return added


def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "20180101"
    with open(WATCHLIST, encoding="utf-8-sig") as file:
        tickers = [row["ticker"] for row in csv.DictReader(file)]
    connection = connect(DEFAULT_PATH)
    connection.execute(SCHEMA)
    failed = []
    for index, ticker in enumerate(tickers, 1):
        try:
            added = collect_ticker(connection, ticker, start)
        except Exception as error:
            failed.append(ticker)
            print(f"[{index}/{len(tickers)}] {ticker} failed: {error}", flush=True)
            continue
        print(f"[{index}/{len(tickers)}] {ticker} +{added}", flush=True)
    total = connection.execute("SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(bizdate) FROM investor_flow").fetchone()
    print(f"done rows={total[0]} tickers={total[1]} oldest={total[2]} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
