from __future__ import annotations

import argparse
import email.utils
import json
import os
import time
import urllib.parse
import urllib.request
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from .app import configured_stocks, load_env
from .dart_reference import corp_code_by_stock
from .point_in_time_store import DEFAULT_PATH, KST, PointInTimeStore, connect, conservative_date_availability, iso_utc


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class PartialCollectionError(RuntimeError):
    """The provider limit was reached before the requested start date."""

    def __init__(self, records: int, coverage_start: date | None, message: str):
        super().__init__(message)
        self.records = records
        self.coverage_start = coverage_start


def collect_news(ticker: str, name: str, start: date, end: date, db, delay: float = .11) -> int:
    client_id, secret = os.environ.get("NAVER_HUB_CLIENT_ID", ""), os.environ.get("NAVER_HUB_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise RuntimeError("NAVER_HUB_CLIENT_ID/NAVER_HUB_CLIENT_SECRET missing")
    before = db.total_changes
    oldest_seen: date | None = None
    reached_requested_start = False
    exhausted_results = False
    for offset in range(1, 1001, 100):
        url = "https://naverapihub.apigw.ntruss.com/search/v1/news?" + urllib.parse.urlencode({"query": name, "display": 100, "start": offset, "sort": "date"})
        request = urllib.request.Request(url, headers={"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": secret, "User-Agent": "stockAlarm-pit/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        items = data.get("items", [])
        if not items:
            exhausted_results = True
            break
        oldest = None
        for item in items:
            published = email.utils.parsedate_to_datetime(item["pubDate"])
            oldest = min(oldest, published.date()) if oldest else published.date()
            oldest_seen = min(oldest_seen, published.date()) if oldest_seen else published.date()
            if not start <= published.date() <= end:
                continue
            title = __import__("re").sub(r"<[^>]+>", " ", item.get("title", "")).strip()
            db.execute("INSERT OR IGNORE INTO news_events VALUES(?,?,?,?,?,?,?,?)",
                       (ticker, title, item.get("originallink") or item.get("link") or "", iso_utc(published), iso_utc(published), _now(), "naver_search_api", json.dumps(item, ensure_ascii=False)))
        db.commit()
        if oldest and oldest < start:
            reached_requested_start = True
            break
        total = int(data.get("total") or 0)
        if len(items) < 100 or (total <= 1000 and offset + len(items) - 1 >= total):
            exhausted_results = True
            break
        time.sleep(delay)
    records = db.total_changes - before
    if not reached_requested_start and not exhausted_results and oldest_seen:
        # If the cap lands inside the requested start date, that boundary day
        # is incomplete too. Start zero-event coverage on the following day.
        coverage_start = oldest_seen if oldest_seen > start else start + timedelta(days=1)
        raise PartialCollectionError(
            records,
            coverage_start,
            f"Naver 1,000-result limit reached; requested start {start.isoformat()} was not reached "
            f"(oldest collected {oldest_seen.isoformat()})",
        )
    return records


def _chunks(start: date, end: date, days: int = 365):
    """Chunk DART requests within its one-year corp-specific search limit."""
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def collect_disclosures(ticker: str, start: date, end: date, db, delay: float = .11, buffer_days: int = 1) -> int:
    key, corp = os.environ.get("DART_API_KEY", ""), corp_code_by_stock(ticker)
    if not key or not corp:
        raise RuntimeError("DART key/corp code missing")
    before = db.total_changes
    for begin, finish in _chunks(start, end):
        page = 1
        while True:
            query = {"crtfc_key": key, "corp_code": corp, "bgn_de": begin.strftime("%Y%m%d"), "end_de": finish.strftime("%Y%m%d"), "page_no": page, "page_count": 100}
            with urllib.request.urlopen("https://opendart.fss.or.kr/api/list.json?" + urllib.parse.urlencode(query), timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            if data.get("status") not in (None, "000", "013"):
                raise RuntimeError(f"DART {data.get('status')} {data.get('message')}")
            items = data.get("list", [])
            for item in items:
                day = datetime.strptime(item["rcept_dt"], "%Y%m%d").date()
                published = datetime.combine(day, datetime.min.time(), KST)
                available = conservative_date_availability(day, buffer_days)
                db.execute("INSERT OR REPLACE INTO disclosure_events VALUES(?,?,?,?,?,?,?,?,?)",
                           (ticker, corp, item["rcept_no"], item.get("report_nm", ""), iso_utc(published), iso_utc(available), "date_only_next_business_day", _now(), json.dumps(item, ensure_ascii=False)))
            db.commit()
            if page * 100 >= int(data.get("total_count") or 0):
                break
            page += 1; time.sleep(delay)
        time.sleep(delay)
    return db.total_changes - before


def collect_financials(ticker: str, start: date, end: date, db) -> int:
    from pykrx import stock
    frame = stock.get_market_fundamental_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
    if frame.empty:
        raise RuntimeError("PyKRX returned no historical fundamentals; KRX authentication/provider availability must be checked")
    before = db.total_changes
    for index, row in frame.iterrows():
        day = index.date() if hasattr(index, "date") else datetime.strptime(str(index)[:10], "%Y-%m-%d").date()
        available = conservative_date_availability(day, 1)
        db.execute("INSERT OR REPLACE INTO financial_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ticker, day.isoformat(), iso_utc(datetime.combine(day, datetime.min.time(), KST)), iso_utc(available),
                    float(row.get("PER", 0) or 0), float(row.get("PBR", 0) or 0), float(row.get("DIV", 0) or 0),
                    float(row.get("EPS", 0) or 0), float(row.get("BPS", 0) or 0), "pykrx_krx_daily",
                    "provider_daily_asof_plus_1_business_day", "provider_revision_history_unverified", _now()))
    db.commit()
    return db.total_changes - before


def collect(start: date, end: date, path: Path = DEFAULT_PATH, sources=("news", "disclosure", "financial"), limit: int = 0) -> dict:
    load_env(); names = configured_stocks(); names = dict(list(names.items())[:limit or None])
    failures = []
    def collect_ticker(ticker: str, name: str) -> list[dict]:
        local_failures = []
        with closing(connect(path)) as db:
            for source in sources:
                try:
                    if source == "news":
                        records = collect_news(ticker, name, start, end, db)
                    elif source == "disclosure":
                        records = collect_disclosures(ticker, start, end, db)
                    elif source == "financial":
                        records = collect_financials(ticker, start, end, db)
                    else:
                        raise ValueError(f"unsupported source: {source}")
                    status, message = "success", ""
                    log_start = start
                except PartialCollectionError as error:
                    records, status, message = error.records, "partial", str(error)
                    log_start = error.coverage_start or end
                    local_failures.append({"ticker": ticker, "source": source, "message": message})
                except Exception as error:
                    records, status, message = 0, "failed", f"{type(error).__name__}:{error}"
                    log_start = start
                    local_failures.append({"ticker": ticker, "source": source, "message": message})
                db.execute("INSERT INTO collection_log(source,ticker,start_date,end_date,status,records,message,collected_at) VALUES(?,?,?,?,?,?,?,?)",
                           (source, ticker, log_start.isoformat(), end.isoformat(), status, records, message, _now())); db.commit()
        return local_failures
    workers = max(1, int(os.environ.get("PIT_COLLECTION_WORKERS", "4")))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(collect_ticker, ticker, name) for ticker, name in names.items()]
        for future in as_completed(futures):
            failures.extend(future.result())
    coverage = PointInTimeStore(path).coverage(list(names), start, end)
    report_dir = Path("reports/backtest/point_in_time"); report_dir.mkdir(parents=True, exist_ok=True)
    import csv
    for filename, rows in (("coverage.csv", coverage), ("failures.csv", failures)):
        with (report_dir / filename).open("w", encoding="utf-8-sig", newline="") as handle:
            if rows:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return {"store": str(path), "tickers": len(names), "failures": len(failures), "coverage": str(report_dir / "coverage.csv")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect point-in-time external factors")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 6, 30)); parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--sources", default="news,disclosure,financial"); parser.add_argument("--limit", type=int, default=0); parser.add_argument("--db", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(); print(json.dumps(collect(args.start, args.end, args.db, tuple(args.sources.split(",")), args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
