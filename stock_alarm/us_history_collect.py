"""Collect US daily OHLCV (Naver) for the top-N market-cap stocks plus SPY/QQQ into data/backtest_us/ohlcv.

Read-only on the live DBs. Universe is today's top-N (survivorship bias remains).
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime,timedelta,time as clock_time
from zoneinfo import ZoneInfo

OUT_DIR = "data/backtest_us"
BASE = "https://api.stock.naver.com/"
ETFS = ["SPY", "QQQ.O"]


def get(path: str):
    request = urllib.request.Request(BASE + path, headers={"User-Agent": "stockAlarm/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def top_by_market_cap(n: int) -> list[dict]:
    rows = []
    for exchange in ("NASDAQ", "NYSE"):
        for page in range(1, -(-n // 100) + 1):  # the API serves at most 100 per page
            data = get(f"stock/exchange/{exchange}/marketValue?page={page}&pageSize=100")
            rows += [s for s in data["stocks"] if s.get("stockEndType") == "stock"]
    rows.sort(key=lambda s: int(s["marketValueRaw"]), reverse=True)
    return rows[:n]


def completed_day(now=None):
    zone=ZoneInfo('America/New_York')
    now=now or datetime.now(zone)
    now=now.replace(tzinfo=now.tzinfo or zone).astimezone(zone)
    return now.date() if now.time()>=clock_time(16,15) else now.date()-timedelta(days=1)


def history(code: str,now=None) -> list[dict]:
    cutoff=completed_day(now)
    end=(cutoff+timedelta(days=1)).strftime('%Y%m%d0000')
    return [r for r in get(f"chart/foreign/item/{code}/day?startDateTime=201801010000&endDateTime={end}")
            if r['localDate']<=cutoff.strftime('%Y%m%d')]


def write_universe(n: int) -> list[dict]:
    os.makedirs(f"{OUT_DIR}/ohlcv", exist_ok=True)
    universe = top_by_market_cap(n)
    with open(f"{OUT_DIR}/universe.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "name_ko", "name_eng", "market_value_usd"])
        for s in universe:
            w.writerow([s["reutersCode"], s.get("stockName", ""), s["stockNameEng"], s["marketValueRaw"]])
    return universe


def refresh(codes: list[str]) -> list[tuple[str, str]]:
    """Re-download full daily history per code; a failed code keeps its previous file. Returns (code, error) failures."""
    failed = []
    for code in codes:
        try:
            rows = history(code)
            if not rows:
                raise ValueError("empty series")
            path = f"{OUT_DIR}/ohlcv/{code}.csv"
            with open(path + ".tmp", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "open", "high", "low", "close", "volume"])
                for r in rows:
                    w.writerow([r["localDate"], r["openPrice"], r["highPrice"], r["lowPrice"], r["closePrice"], r["accumulatedTradingVolume"]])
            os.replace(path + ".tmp", path)
        except Exception as exc:  # keep going; report at the end
            failed.append((code, str(exc)))
        time.sleep(0.2)
    return failed


def main(n: int = 100) -> None:
    """Build today's top-N universe (overwrites universe.csv) and download it. Daily updates use refresh() instead."""
    universe = write_universe(n)
    failed = refresh([s["reutersCode"] for s in universe] + ETFS)
    print(f"universe={len(universe)} failed={failed}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
