"""Collect full DART financial statements (fnlttSinglAcntAll) for a ticker list.

The existing collector stores only five ratios (financial_statement_reference),
which cannot answer revenue-amount, gross-margin, EBITDA, cash-flow or DCF
questions. This stores the filing's raw account lines instead -- income
statement, balance sheet and cash-flow statement -- so those are all derivable
later without re-downloading anything.

Quarterly/half-year filings are year-to-date cumulative, so a single quarter is
thstrm(this report) - thstrm(previous report of the same year); the stored rows
keep every filed figure untouched -- including thstrm_add_amount, the
year-to-date total a quarterly income statement reports alongside the
three-month thstrm_amount -- and leave the arithmetic to financial_metrics.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from .dart_reference import corp_code_by_stock
from .point_in_time_store import DEFAULT_PATH, connect

API_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
# 1Q, half-year, 3Q, annual -- in filing order within a business year.
REPORT_CODES = ["11013", "11012", "11014", "11011"]
REPORT_LABELS = {"11013": "1Q", "11012": "2Q_cum", "11014": "3Q_cum", "11011": "FY"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS financial_statement_lines(
 ticker TEXT NOT NULL, bsns_year INTEGER NOT NULL, reprt_code TEXT NOT NULL, fs_div TEXT NOT NULL,
 sj_div TEXT NOT NULL, account_id TEXT NOT NULL, account_nm TEXT NOT NULL, account_detail TEXT NOT NULL DEFAULT '',
 thstrm_amount REAL, thstrm_add_amount REAL, frmtrm_amount REAL, frmtrm_q_amount REAL, bfefrmtrm_amount REAL,
 ord INTEGER, currency TEXT NOT NULL DEFAULT '',
 collected_at TEXT NOT NULL,
 PRIMARY KEY(ticker, bsns_year, reprt_code, fs_div, sj_div, account_id, account_nm, account_detail));
CREATE INDEX IF NOT EXISTS idx_fs_lines_ticker ON financial_statement_lines(ticker, bsns_year, reprt_code);
"""


class DartQuotaExceeded(RuntimeError):
    """DART's daily call limit -- stop the whole run, retrying just burns time."""


def _amount(raw) -> float | None:
    text = str(raw or "").replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def report_periods(count: int, today: date | None = None) -> list[tuple[int, str]]:
    """The `count` most recent (year, reprt_code) pairs, newest first.

    Filing windows are not modelled: a period that has not been filed yet
    simply returns no rows and is skipped by collect_ticker().
    """
    today = today or date.today()
    periods = [
        (year, code)
        for year in range(today.year, today.year - count // 2 - 3, -1)
        for code in reversed(REPORT_CODES)
    ]
    return periods[:count]


def fetch_report(corp_code: str, key: str, year: int, reprt_code: str, fs_div: str = "CFS", timeout: int = 20) -> list[dict]:
    url = f"{API_URL}?" + urllib.parse.urlencode(
        {"crtfc_key": key, "corp_code": corp_code, "bsns_year": year, "reprt_code": reprt_code, "fs_div": fs_div}
    )
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    status = str(data.get("status", ""))
    if status == "020":
        raise DartQuotaExceeded(data.get("message", "daily limit"))
    if status != "000":
        return []
    return data.get("list") or []


def store_lines(db, ticker: str, year: int, reprt_code: str, fs_div: str, rows: list[dict]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    db.executemany(
        "INSERT OR REPLACE INTO financial_statement_lines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (ticker, year, reprt_code, fs_div, row.get("sj_div", ""), row.get("account_id", ""),
             row.get("account_nm", ""), row.get("account_detail", "") or "",
             _amount(row.get("thstrm_amount")), _amount(row.get("thstrm_add_amount")),
             _amount(row.get("frmtrm_amount")), _amount(row.get("frmtrm_q_amount")), _amount(row.get("bfefrmtrm_amount")),
             int(row.get("ord") or 0), row.get("currency", "") or "", now)
            for row in rows
        ],
    )
    db.commit()
    return len(rows)


def collect_ticker(ticker: str, periods: list[tuple[int, str]], db, key: str, delay: float = 0.2) -> dict:
    corp_code = corp_code_by_stock(ticker)
    if not corp_code:
        return {"ticker": ticker, "status": "no_corp_code", "periods": 0, "rows": 0}
    stored_periods = stored_rows = 0
    for year, reprt_code in periods:
        rows = fetch_report(corp_code, key, year, reprt_code)
        fs_div = "CFS"
        if not rows:  # smaller filers report a separate (OFS) statement only
            rows = fetch_report(corp_code, key, year, reprt_code, fs_div="OFS")
            fs_div = "OFS"
        if rows:
            stored_rows += store_lines(db, ticker, year, reprt_code, fs_div, rows)
            stored_periods += 1
        time.sleep(delay)
    return {"ticker": ticker, "status": "success" if stored_periods else "no_data",
            "periods": stored_periods, "rows": stored_rows}


def collected_tickers(path: Path = DEFAULT_PATH) -> set[str]:
    """Tickers that already have stored lines, so a rerun can skip them."""
    with closing(connect(path)) as db:
        db.executescript(SCHEMA)
        return {row[0] for row in db.execute("SELECT DISTINCT ticker FROM financial_statement_lines")}


def universe_tickers(dynamic: bool = False) -> list[str]:
    """The static watchlist, or today's wider screening universe."""
    from .app import configured_stocks, recommend_universe
    import os as _os

    if not dynamic:
        return list(configured_stocks())
    return list(recommend_universe(int(_os.environ.get("MIN_TRADING_VALUE", "5000000000"))))


def collect(tickers: list[str] | None = None, quarters: int = 8, path: Path = DEFAULT_PATH, delay: float = 0.2) -> dict:
    from .app import configured_stocks, load_env

    load_env()
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        return {"error": "DART_API_KEY missing"}
    tickers = tickers or list(configured_stocks())
    periods = report_periods(quarters)
    results, quota_hit = [], ""
    with closing(connect(path)) as db:
        db.executescript(SCHEMA)
        for ticker in tickers:
            try:
                result = collect_ticker(ticker, periods, db, key, delay)
            except DartQuotaExceeded as error:
                quota_hit = str(error)
                break
            except Exception as error:
                result = {"ticker": ticker, "status": f"failed:{type(error).__name__}", "periods": 0, "rows": 0}
            results.append(result)
            db.execute(
                "INSERT INTO collection_log(source,ticker,start_date,end_date,status,records,message,collected_at) VALUES(?,?,?,?,?,?,?,?)",
                ("financial_statement_lines", ticker, str(periods[-1][0]), str(periods[0][0]), result["status"],
                 result["rows"], "", datetime.now().isoformat(timespec="seconds")),
            )
            db.commit()
            print(f"{ticker} {result['status']} periods={result['periods']} rows={result['rows']}", flush=True)
    return {
        "store": str(path), "tickers": len(results), "quarters": quarters,
        "succeeded": sum(r["status"] == "success" for r in results),
        "rows": sum(r["rows"] for r in results),
        "quota_exceeded": quota_hit,
        "failures": [r for r in results if r["status"] != "success"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect full DART financial statements for the watchlist")
    parser.add_argument("--quarters", type=int, default=8, help="most recent report periods per ticker")
    parser.add_argument("--tickers", default="", help="comma-separated tickers (default: watchlist)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--db", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--dynamic-universe", action="store_true",
                        help="collect today's screening universe (DYNAMIC_SCREENING_TOP_N) instead of the watchlist")
    parser.add_argument("--skip-collected", action="store_true", help="skip tickers that already have stored lines")
    parser.add_argument("--stored", action="store_true",
                        help="also refresh every ticker already stored, not just the chosen universe")
    args = parser.parse_args()
    from .app import load_env

    load_env()
    tickers = [value.strip() for value in args.tickers.split(",") if value.strip()]
    if not tickers:
        tickers = universe_tickers(args.dynamic_universe)
    if args.stored:
        # A weekly refresh has to cover what was collected before, not just
        # whatever happens to screen well today.
        tickers = sorted(set(tickers) | collected_tickers(args.db))
    if args.skip_collected:
        done = collected_tickers(args.db)
        tickers = [ticker for ticker in tickers if ticker not in done]
    if args.limit:
        tickers = tickers[:args.limit]
    print(json.dumps(collect(tickers, args.quarters, args.db, args.delay), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
