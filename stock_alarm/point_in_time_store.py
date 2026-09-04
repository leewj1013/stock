from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .dart_reference import keyword_score as disclosure_keyword_score
from .fundamental_reference import _score as financial_score
from .news_reference import keyword_score as news_keyword_score

KST = ZoneInfo("Asia/Seoul")
DEFAULT_PATH = Path("data/backtest/point_in_time.sqlite3")


SCHEMA = """
CREATE TABLE IF NOT EXISTS news_events(
 ticker TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL,
 available_at TEXT NOT NULL, collected_at TEXT NOT NULL, source TEXT NOT NULL, raw_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(ticker, title, published_at));
CREATE INDEX IF NOT EXISTS idx_news_asof ON news_events(ticker, available_at);
CREATE TABLE IF NOT EXISTS disclosure_events(
 ticker TEXT NOT NULL, corp_code TEXT NOT NULL, receipt_no TEXT NOT NULL, report_name TEXT NOT NULL,
 published_at TEXT NOT NULL, available_at TEXT NOT NULL, timestamp_precision TEXT NOT NULL,
 collected_at TEXT NOT NULL, raw_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(receipt_no));
CREATE INDEX IF NOT EXISTS idx_disclosure_asof ON disclosure_events(ticker, available_at);
CREATE TABLE IF NOT EXISTS financial_snapshots(
 ticker TEXT NOT NULL, metric_date TEXT NOT NULL, published_at TEXT NOT NULL, available_at TEXT NOT NULL,
 per REAL, pbr REAL, dividend_yield REAL, eps REAL, bps REAL, source TEXT NOT NULL,
 availability_method TEXT NOT NULL, revision_status TEXT NOT NULL, collected_at TEXT NOT NULL,
 PRIMARY KEY(ticker, metric_date, source));
CREATE INDEX IF NOT EXISTS idx_financial_asof ON financial_snapshots(ticker, available_at);
CREATE TABLE IF NOT EXISTS financial_statement_snapshots(
 ticker TEXT NOT NULL, bsns_year INTEGER NOT NULL, reprt_code TEXT NOT NULL,
 published_at TEXT NOT NULL, available_at TEXT NOT NULL,
 roe_pct REAL, debt_ratio_pct REAL, operating_margin_pct REAL,
 revenue_growth_pct REAL, operating_income_growth_pct REAL,
 source TEXT NOT NULL, collected_at TEXT NOT NULL,
 PRIMARY KEY(ticker, bsns_year, reprt_code));
CREATE INDEX IF NOT EXISTS idx_financial_statement_asof ON financial_statement_snapshots(ticker, available_at);
CREATE TABLE IF NOT EXISTS collection_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, ticker TEXT NOT NULL, start_date TEXT, end_date TEXT,
 status TEXT NOT NULL, records INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', collected_at TEXT NOT NULL);
"""


def connect(path: Path = DEFAULT_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # Another collection worker may be committing. Existing WAL mode is
        # sufficient; busy_timeout protects subsequent reads and writes.
        pass
    connection.executescript(SCHEMA)
    return connection


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=KST)
    return value.astimezone(timezone.utc).isoformat()


def signal_cutoff(day: date) -> str:
    """The close-based backtest may only see records public by 15:30 KST."""
    return iso_utc(datetime.combine(day, time(15, 30), KST))


def conservative_date_availability(day: date, buffer_business_days: int = 1) -> datetime:
    """Date-only disclosures become usable after N weekday buffers.

    This intentionally skips weekends but cannot infer Korean exchange holidays;
    using midnight after the buffer is conservative relative to same-day use.
    """
    current, remaining = day, max(1, int(buffer_business_days))
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return datetime.combine(current, time(0, 0), KST)


class PointInTimeStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path

    def scores_asof(self, ticker: str, day: date, news_window_days: int = 30, disclosure_window_days: int = 30) -> dict:
        cutoff = signal_cutoff(day)
        with closing(connect(self.path)) as db:
            news = db.execute("SELECT title FROM news_events WHERE ticker=? AND available_at<=? AND published_at>=? ORDER BY published_at DESC",
                              (ticker, cutoff, iso_utc(datetime.combine(day - timedelta(days=news_window_days), time.min, KST)))).fetchall()
            disclosures = db.execute("SELECT report_name FROM disclosure_events WHERE ticker=? AND available_at<=? AND published_at>=? ORDER BY published_at DESC",
                                     (ticker, cutoff, iso_utc(datetime.combine(day - timedelta(days=disclosure_window_days), time.min, KST)))).fetchall()
            financial = db.execute("SELECT * FROM financial_snapshots WHERE ticker=? AND available_at<=? ORDER BY available_at DESC LIMIT 1",
                                   (ticker, cutoff)).fetchone()
            statement = db.execute(
                "SELECT * FROM financial_statement_snapshots WHERE ticker=? AND available_at<=? ORDER BY available_at DESC LIMIT 1",
                (ticker, cutoff),
            ).fetchone()
            collected = {row["source"] for row in db.execute(
                "SELECT DISTINCT source FROM collection_log WHERE ticker=? AND status IN ('success','partial') AND start_date<=? AND end_date>=?",
                (ticker, day.isoformat(), day.isoformat()),
            ).fetchall()}
        news_value, _ = news_keyword_score([row[0] for row in news])
        disclosure_value, _ = disclosure_keyword_score([row[0] for row in disclosures])
        financial_value = financial_score(float(financial["per"] or 0), float(financial["pbr"] or 0),
                                          float(financial["dividend_yield"] or 0), float(financial["eps"] or 0),
                                          float(financial["bps"] or 0)) if financial else 0.0
        # A successfully collected interval makes a zero-event day an observed
        # zero, not missing data. Financials additionally require a usable as-of row.
        sources = {"news": "news" in collected or bool(news), "disclosure": "disclosure" in collected or bool(disclosures),
                   "financial": bool(financial)}
        dividend_yield = float(financial["dividend_yield"] or 0) if financial else 0.0
        financial_ratios = {
            "roe_pct": statement["roe_pct"], "debt_ratio_pct": statement["debt_ratio_pct"],
            "operating_margin_pct": statement["operating_margin_pct"], "revenue_growth_pct": statement["revenue_growth_pct"],
            "operating_income_growth_pct": statement["operating_income_growth_pct"],
        } if statement else {}
        sources["financial_statement"] = bool(statement)
        return {"news_score": float(news_value), "disclosure_score": float(disclosure_value),
                "financial_score": float(financial_value), "dividend_yield": dividend_yield,
                "financial_ratios": financial_ratios, "pit_sources": sources,
                "external_factor_status": "available_point_in_time" if any(sources.values()) else "unavailable_point_in_time_snapshot"}

    def coverage(self, tickers: list[str], start: date, end: date) -> list[dict]:
        with closing(connect(self.path)) as db:
            rows = []
            for ticker in tickers:
                for source, table in (("news", "news_events"), ("disclosure", "disclosure_events"), ("financial", "financial_snapshots"), ("financial_statement", "financial_statement_snapshots")):
                    item = db.execute(f"SELECT COUNT(*) count, MIN(published_at) first_at, MAX(published_at) last_at FROM {table} WHERE ticker=?", (ticker,)).fetchone()
                    collection = db.execute(
                        "SELECT status,start_date,end_date,message FROM collection_log WHERE ticker=? AND source=? ORDER BY collected_at DESC LIMIT 1",
                        (ticker, source),
                    ).fetchone()
                    status = collection["status"] if collection else ("available" if item["count"] else "unavailable")
                    rows.append({"ticker": ticker, "source": source, "records": int(item["count"]),
                                 "first_published_at": item["first_at"] or "", "last_published_at": item["last_at"] or "",
                                 "requested_start": start.isoformat(), "requested_end": end.isoformat(),
                                 "status": status,
                                 "coverage_start": collection["start_date"] if collection else "",
                                 "coverage_end": collection["end_date"] if collection else "",
                                 "message": collection["message"] if collection else ""})
        return rows
