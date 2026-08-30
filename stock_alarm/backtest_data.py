from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from .app import configured_stocks, env_date, load_env, naver_rows
from .data_quality import validate_price_rows


DATA_DIR = Path("data/backtest/ohlcv")
REPORT_DIR = Path("reports/backtest")
BENCHMARK = "KOSPI"


def validate_backtest_rows(ticker: str, rows: list[list]) -> tuple[list[list], list[dict[str, str]]]:
    """Apply the live OHLCV checks to every historical row and remove duplicates."""
    valid, issues, seen = [], [], set()
    for row in sorted(rows, key=lambda item: str(item[0])):
        raw_day = str(row[0]) if row else ""
        try:
            expected = datetime.strptime(raw_day, "%Y%m%d").date()
        except (TypeError, ValueError):
            issues.append({"ticker": ticker, "date": raw_day, "reason": "unparseable_date"})
            continue
        if raw_day in seen:
            issues.append({"ticker": ticker, "date": raw_day, "reason": "duplicate_date"})
            continue
        seen.add(raw_day)
        check = validate_price_rows(ticker, [row], expected_day=expected, source="naver_backtest")
        if check["status"] != "valid":
            issues.append({"ticker": ticker, "date": raw_day, "reason": check["reason"] or check["status"]})
            continue
        valid.append([raw_day, *[int(row[index]) for index in range(1, 6)]])
    return valid, issues


def write_rows(ticker: str, rows: list[list], directory: Path = DATA_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ticker}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        writer.writerows(rows)
    return path


def read_rows(ticker: str, directory: Path = DATA_DIR) -> list[list]:
    path = directory / f"{ticker}.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as file:
        return [[row["date"], *[int(row[key]) for key in ("open", "high", "low", "close", "volume")]] for row in csv.DictReader(file)]


def label_market_regimes(
    benchmark_rows: list[list],
    ma_days: int = 120,
    return_days: int = 60,
    trend_threshold_pct: float = 5.0,
) -> list[dict[str, str]]:
    labels = []
    closes = [float(row[4]) for row in benchmark_rows]
    warmup = max(ma_days, return_days)
    for index in range(warmup, len(benchmark_rows)):
        close = closes[index]
        ma = sum(closes[index - ma_days + 1:index + 1]) / ma_days
        momentum = (close / closes[index - return_days] - 1) * 100
        if close > ma and momentum >= trend_threshold_pct:
            regime = "bull"
        elif close < ma and momentum <= -trend_threshold_pct:
            regime = "bear"
        else:
            regime = "sideways"
        labels.append({
            "date": datetime.strptime(str(benchmark_rows[index][0]), "%Y%m%d").date().isoformat(),
            "regime": regime, "close": f"{close:.2f}", "ma120": f"{ma:.2f}",
            "return_60d_pct": f"{momentum:.4f}",
        })
    return labels


def _missing_session_issues(ticker: str, rows: list[list], benchmark_dates: set[str]) -> list[dict[str, str]]:
    if not rows:
        return [{"ticker": ticker, "date": "", "reason": "no_valid_rows"}]
    dates = {str(row[0]) for row in rows}
    first, last = min(dates), max(dates)
    return [
        {"ticker": ticker, "date": day, "reason": "missing_benchmark_session"}
        for day in sorted(benchmark_dates)
        if first <= day <= last and day not in dates
    ]


def collect(
    start_day: date | None = None,
    end_day: date | None = None,
    directory: Path = DATA_DIR,
    report_dir: Path = REPORT_DIR,
    tickers: dict[str, str] | None = None,
    workers: int = 1,
) -> dict:
    load_env()
    start_day = start_day or env_date("BACKTEST_START_DATE", date(2022, 1, 1))
    end_day = end_day or env_date("BACKTEST_END_DATE", date.today())
    tickers = {BENCHMARK: "KOSPI", **(tickers if tickers is not None else configured_stocks())}
    datasets, issues = {}, []
    def fetch(ticker: str) -> tuple[str, list[list], list[dict[str, str]]]:
        try:
            raw = naver_rows(ticker, start_day, end_day)
        except Exception as error:
            raw = []
            return ticker, [], [{"ticker": ticker, "date": "", "reason": f"provider_error:{type(error).__name__}"}]
        valid, row_issues = validate_backtest_rows(ticker, raw)
        return ticker, valid, row_issues

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            fetched = executor.map(fetch, tickers)
            for ticker, valid, row_issues in fetched:
                datasets[ticker] = valid
                issues.extend(row_issues)
                write_rows(ticker, valid, directory)
    else:
        for ticker in tickers:
            ticker, valid, row_issues = fetch(ticker)
            datasets[ticker] = valid
            issues.extend(row_issues)
            write_rows(ticker, valid, directory)
    benchmark_dates = {str(row[0]) for row in datasets.get(BENCHMARK, [])}
    for ticker, rows in datasets.items():
        if ticker != BENCHMARK:
            issues.extend(_missing_session_issues(ticker, rows, benchmark_dates))
    report_dir.mkdir(parents=True, exist_ok=True)
    with (report_dir / "data_quality.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["ticker", "date", "reason"])
        writer.writeheader()
        writer.writerows(issues)
    regimes = label_market_regimes(
        datasets.get(BENCHMARK, []),
        int(os.environ.get("BACKTEST_REGIME_MA_DAYS", "120")),
        int(os.environ.get("BACKTEST_REGIME_RETURN_DAYS", "60")),
        float(os.environ.get("BACKTEST_REGIME_TREND_PCT", "5")),
    )
    with (report_dir / "regime_labels.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["date", "regime", "close", "ma120", "return_60d_pct"])
        writer.writeheader()
        writer.writerows(regimes)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"), "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(), "source": "Naver Finance daily OHLCV", "benchmark": BENCHMARK,
        "ticker_count": len(tickers) - 1, "valid_ticker_count": sum(bool(rows) for ticker, rows in datasets.items() if ticker != BENCHMARK),
        "quality_issue_count": len(issues), "regime_counts": {name: sum(row["regime"] == name for row in regimes) for name in ("bull", "bear", "sideways")},
        "actual_start_date": min((str(row[0]) for rows in datasets.values() for row in rows), default=""),
        "actual_end_date": max((str(row[0]) for rows in datasets.values() for row in rows), default=""),
        "valid_row_count": sum(len(rows) for rows in datasets.values()),
        "collection_workers": workers,
    }
    (report_dir / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return manifest


if __name__ == "__main__":
    collect()
