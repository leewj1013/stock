from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Callable

from .data_store import record_price_quality


def validate_price_rows(
    ticker: str,
    rows: list[list],
    expected_day: date | None = None,
    source: str = "naver",
    reference_close: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now()
    expected_day = expected_day or now.date()
    status, reasons = "valid", []
    close = None
    price_day = None
    if not rows:
        status, reasons = "invalid", ["missing_price"]
    else:
        row = rows[-1]
        try:
            price_day = datetime.strptime(str(row[0]), "%Y%m%d").date()
            open_price, high, low, close, volume = (int(row[index]) for index in range(1, 6))
            if min(open_price, high, low, close) <= 0 or volume < 0 or high < max(open_price, close) or low > min(open_price, close):
                status, reasons = "invalid", ["invalid_ohlc"]
            if price_day != expected_day:
                status, reasons = "stale", [f"price_date={price_day.isoformat()}"]
            if reference_close and close and abs(close - reference_close) / reference_close * 100 > float(os.environ.get("PRICE_CLOSE_TOLERANCE_PCT", "0.5")):
                status, reasons = "quarantined", ["close_source_mismatch"]
        except (TypeError, ValueError, IndexError):
            status, reasons = "invalid", ["unparseable_price"]
    result = {
        "created_at": now.isoformat(timespec="seconds"), "ticker": ticker,
        "price_date": price_day.isoformat() if price_day else "", "source": source,
        "reference_source": "pykrx" if reference_close else "", "close": close,
        "reference_close": reference_close, "age_minutes": 0 if price_day == expected_day else None,
        "status": status, "reason": ",".join(reasons),
    }
    return result


def checked_prices(
    tickers: list[str],
    row_provider: Callable[[str], list[list]],
    expected_day: date,
    path: str = "data/stock_alarm.db",
    reference_provider: Callable[[str], int | None] | None = None,
    reference_name: str = "",
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    prices, checks = {}, []
    for ticker in tickers:
        try:
            rows = row_provider(ticker)
        except Exception as error:
            rows = []
            provider_error = type(error).__name__
        else:
            provider_error = ""
        try:
            reference_close = reference_provider(ticker) if reference_provider and rows else None
        except Exception:
            reference_close = None
        check = validate_price_rows(ticker, rows, expected_day, reference_close=reference_close)
        if reference_provider and rows and reference_close is None:
            check["reference_source"] = reference_name or "reference"
            check["reason"] = ",".join(filter(None, [check.get("reason"), "reference_unavailable"]))
        if provider_error:
            check["reason"] = f"provider_error:{provider_error}"
        record_price_quality(check, path)
        checks.append(check)
        if check["status"] == "valid" and check.get("close"):
            prices[ticker] = int(check["close"])
    return prices, checks
