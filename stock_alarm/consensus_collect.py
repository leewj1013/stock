"""Weekly analyst-consensus history (EPS, net income, target price) per
ticker from WiseReport (the data behind Naver's 컨센서스 tab), into the
point-in-time store.

WiseReport only serves a rolling ~13-week window of weekly points, so history
exists only as far back as this has been run: run at least every ~10 weeks or
points are lost for good. Each weekly point is the consensus as published on
that date, so it may feed decisions from the next session on.

    python -m stock_alarm.consensus_collect
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from datetime import date, datetime

from .point_in_time_store import DEFAULT_PATH, connect

BASE = "https://navercomp.wisereport.co.kr/company/ajax/"
REFERER = "https://navercomp.wisereport.co.kr/v2/company/c1050001.aspx?cmp_cd={ticker}"
WATCHLISTS = ("data/backtest_expanded/experimental_watchlist.csv", "data/watchlist.csv")
SCHEMA = """CREATE TABLE IF NOT EXISTS consensus_weekly(
    ticker TEXT NOT NULL, fiscal_period TEXT NOT NULL, week_date TEXT NOT NULL,
    eps REAL, net_income REAL, target_price REAL, close_price REAL, collected_at TEXT NOT NULL,
    PRIMARY KEY (ticker, fiscal_period, week_date))"""


def _get(path: str, ticker: str) -> dict:
    request = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0 stockAlarm consensus", "Referer": REFERER.format(ticker=ticker)})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8", "ignore") or "{}")


def fiscal_periods(ticker: str, today: str) -> list[str]:
    """The current fiscal year (flagged CK=1) and the one after it."""
    items = _get(f"c1050001_data.aspx?flag=3&cmp_cd={ticker}&finGubun=MAIN&frq=0&sDT={today}&chartType=svg", ticker).get("JsonData") or []
    periods = [item["YYMM"] for item in items]
    current = next((item["YYMM"] for item in items if item.get("CK") == 1), None)
    if current is None:
        return []
    return periods[periods.index(current):periods.index(current) + 2]


def weekly_rows(ticker: str, period: str, today: str, collected_at: str) -> list[tuple]:
    data = _get(f"cF5001.aspx?cmp_cd={ticker}&dt={today}&yymm={period}&frq=0&acc_cd=122700&fingubun=MAIN&chartType=svg", ticker)
    if not data.get("chart1"):
        return []
    eps, income = json.loads(data["chart1"]), json.loads(data.get("chart2") or "{}")
    net = income.get("select_item") or [None] * len(eps["categories"])
    return [
        (ticker, period, day.replace("/", "-"), eps["select_item"][i], net[i], eps["target_price"][i], eps["close_price"][i], collected_at)
        for i, day in enumerate(eps["categories"])
    ]


def tickers() -> list[str]:
    seen: dict[str, None] = {}
    for path in WATCHLISTS:
        try:
            with open(path, encoding="utf-8-sig") as file:
                for row in csv.DictReader(file):
                    seen[str(row.get("ticker", "")).zfill(6)] = None
        except FileNotFoundError:
            continue
    return [ticker for ticker in seen if ticker.strip("0")]


def main() -> None:
    today = date.today().strftime("%Y%m%d")
    collected_at = datetime.now().isoformat(timespec="seconds")
    connection = connect(DEFAULT_PATH)
    connection.execute(SCHEMA)
    universe = tickers()
    stored, without, failed = 0, 0, []
    for ticker in universe:
        try:
            rows = [row for period in fiscal_periods(ticker, today) for row in weekly_rows(ticker, period, today, collected_at)]
        except Exception as error:
            failed.append(f"{ticker}:{type(error).__name__}")
            continue
        if not rows:
            without += 1  # no analyst coverage
        connection.executemany("INSERT OR IGNORE INTO consensus_weekly VALUES (?,?,?,?,?,?,?,?)", rows)
        connection.commit()
        stored += len(rows)
        time.sleep(0.3)
    total = connection.execute("SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(week_date), MAX(week_date) FROM consensus_weekly").fetchone()
    print(f"consensus tickers={len(universe)} no_coverage={without} new_rows_seen={stored} table={tuple(total)} failed={failed[:20]}", flush=True)


if __name__ == "__main__":
    main()
