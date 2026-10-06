"""Collect US daily OHLCV (Naver) for the top-N market-cap stocks plus SPY/QQQ into data/backtest_us/ohlcv.

Read-only on the live DBs. Universe is today's top-N (survivorship bias remains).
"""
import csv
import json
import os
import sys
import time
import urllib.request

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
        data = get(f"stock/exchange/{exchange}/marketValue?page=1&pageSize={n}")
        rows += [s for s in data["stocks"] if s.get("stockEndType") == "stock"]
    rows.sort(key=lambda s: int(s["marketValueRaw"]), reverse=True)
    return rows[:n]


def history(code: str) -> list[dict]:
    return get(f"chart/foreign/item/{code}/day?startDateTime=201801010000&endDateTime=202612310000")


def main(n: int = 100) -> None:
    os.makedirs(f"{OUT_DIR}/ohlcv", exist_ok=True)
    universe = top_by_market_cap(n)
    with open(f"{OUT_DIR}/universe.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "name_eng", "market_value_usd"])
        for s in universe:
            w.writerow([s["reutersCode"], s["stockNameEng"], s["marketValueRaw"]])
    failed = []
    for code in [s["reutersCode"] for s in universe] + ETFS:
        try:
            rows = history(code)
            with open(f"{OUT_DIR}/ohlcv/{code}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "open", "high", "low", "close", "volume"])
                for r in rows:
                    w.writerow([r["localDate"], r["openPrice"], r["highPrice"], r["lowPrice"], r["closePrice"], r["accumulatedTradingVolume"]])
        except Exception as exc:  # keep going; report at the end
            failed.append((code, str(exc)))
        time.sleep(0.2)
    print(f"universe={len(universe)} failed={failed}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
