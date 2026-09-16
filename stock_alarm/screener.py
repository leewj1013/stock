"""Value/growth screening over the collected DART fundamentals.

Joins three sources that already exist:

* KRX daily fundamentals (PER/PBR/EPS + KOSPI/KOSDAQ market), cached per day
  because the bulk call needs a KRX login and is slow.
* financial_metrics quarterly rows -> latest year-over-year growth.
* financial_metrics trailing twelve months -> free cash flow.

Every filter is optional; a ticker is only rejected by a filter whose input is
actually available, and tickers missing an input for an active filter are
reported separately rather than silently dropped.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .financial_metrics import all_tickers, quarterly_metrics, trailing_twelve_months
from .point_in_time_store import DEFAULT_PATH

FUNDAMENTAL_CACHE = Path(".cache/krx_fundamental.json")
# The dashboard reads this instead of re-screening 400+ tickers on every render.
LATEST_RESULT = Path("reports/fundamentals/screen_latest.json")


@dataclass
class Filters:
    per_max: float | None = None
    per_min: float | None = 0.0  # a non-positive PER means "loss-making", not "cheap"
    pbr_max: float | None = None
    dividend_min: float | None = None
    revenue_growth_min: float | None = None
    operating_income_growth_min: float | None = None
    positive_free_cash_flow: bool = False
    markets: tuple[str, ...] = field(default_factory=tuple)

    def required(self) -> list[str]:
        names = []
        if self.per_max is not None or self.per_min is not None:
            names.append("per")
        if self.pbr_max is not None:
            names.append("pbr")
        if self.dividend_min is not None:
            names.append("dividend_yield")
        if self.revenue_growth_min is not None:
            names.append("revenue_growth_pct")
        if self.operating_income_growth_min is not None:
            names.append("operating_income_growth_pct")
        if self.positive_free_cash_flow:
            names.append("free_cash_flow")
        return names


def has_priced_rows(data: dict, minimum_ratio: float = 0.2) -> bool:
    """True when enough tickers carry a real PER for the day to be usable."""
    if not data:
        return False
    priced = sum(1 for row in data.values() if row.get("per"))
    return priced / len(data) >= minimum_ratio


def load_market_fundamentals(as_of: date | None = None, cache_path: Path = FUNDAMENTAL_CACHE, refresh: bool = False) -> dict:
    """{ticker: {market, per, pbr, eps, dividend_yield}} for KOSPI + KOSDAQ."""
    if cache_path.exists() and not refresh:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if as_of is not None:
            if cached.get("as_of") == as_of.strftime("%Y%m%d"):
                return cached
        # No explicit date: reuse the cache only while it was written today,
        # otherwise a scheduled daily run would screen on stale prices forever.
        elif date.fromtimestamp(cache_path.stat().st_mtime) == date.today():
            return cached
    from pykrx import stock

    from .app import load_env

    load_env()  # the bulk KRX endpoint needs KRX_ID/KRX_PW from the secure store
    day = as_of or date.today()
    data: dict[str, dict] = {}
    resolved = ""
    for back in range(7):
        stamp = (day - timedelta(days=back)).strftime("%Y%m%d")
        day_data: dict[str, dict] = {}
        for market in ("KOSPI", "KOSDAQ"):
            frame = stock.get_market_fundamental_by_ticker(stamp, market=market)
            for ticker, row in frame.iterrows():
                day_data[ticker] = {"market": market, "per": float(row["PER"]), "pbr": float(row["PBR"]),
                                    "eps": float(row["EPS"]), "dividend_yield": float(row["DIV"])}
        # Before the session settles, KRX answers with a full ticker list whose
        # ratios are all zero -- accepting that would screen every stock as
        # "PER 0" and match nothing, so fall back to the previous day.
        if has_priced_rows(day_data):
            data, resolved = day_data, stamp
            break
    payload = {"as_of": resolved, "data": data}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def candidate_rows(path: Path = DEFAULT_PATH, names: dict[str, str] | None = None) -> list[dict]:
    """One row per ticker: latest growth plus trailing-twelve-month cash flow."""
    names = names or {}
    rows = []
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        for ticker in all_tickers(db):
            quarters = quarterly_metrics(db, ticker)
            if not quarters:
                continue
            latest = quarters[-1]
            ttm = trailing_twelve_months(quarters)
            rows.append({
                "ticker": ticker, "name": names.get(ticker, ""), "period": latest["period"],
                "revenue_growth_pct": latest.get("revenue_growth_pct"),
                "operating_income_growth_pct": latest.get("operating_income_growth_pct"),
                "operating_margin_pct": latest.get("operating_margin_pct"),
                "gross_margin_pct": latest.get("gross_margin_pct"),
                "free_cash_flow": ttm.get("free_cash_flow"), "ttm_revenue": ttm.get("revenue"),
                "ttm_operating_income": ttm.get("operating_income"),
            })
    return rows


def apply_filters(rows: list[dict], fundamentals: dict, filters: Filters) -> tuple[list[dict], list[dict]]:
    """(passing rows, rows skipped for missing inputs), newest data joined in."""
    passed, incomplete = [], []
    required = filters.required()
    for row in rows:
        market = fundamentals.get(row["ticker"])
        merged = {**row, **{key: (market or {}).get(key) for key in ("market", "per", "pbr", "eps", "dividend_yield")}}
        missing = [name for name in required if merged.get(name) is None]
        if missing:
            incomplete.append({**merged, "missing": ",".join(missing)})
            continue
        if filters.markets and merged.get("market") not in filters.markets:
            continue
        checks = (
            (filters.per_max is None or merged["per"] < filters.per_max),
            (filters.per_min is None or merged["per"] > filters.per_min),
            (filters.pbr_max is None or merged["pbr"] < filters.pbr_max),
            (filters.dividend_min is None or merged["dividend_yield"] >= filters.dividend_min),
            (filters.revenue_growth_min is None or merged["revenue_growth_pct"] > filters.revenue_growth_min),
            (filters.operating_income_growth_min is None or merged["operating_income_growth_pct"] > filters.operating_income_growth_min),
            (not filters.positive_free_cash_flow or merged["free_cash_flow"] > 0),
        )
        if all(checks):
            passed.append(merged)
    passed.sort(key=lambda item: (item.get("per") is None, item.get("per")))
    return passed, incomplete


def screen(filters: Filters, path: Path = DEFAULT_PATH, as_of: date | None = None, refresh: bool = False) -> dict:
    from .app import configured_stocks

    names = dict(configured_stocks())
    try:
        with closing(sqlite3.connect("data/stock_alarm.db")) as db:
            for ticker, name in db.execute("SELECT DISTINCT ticker, name FROM candidate_snapshots WHERE name IS NOT NULL"):
                names.setdefault(ticker, name)
    except sqlite3.DatabaseError:
        pass
    fundamentals = load_market_fundamentals(as_of, refresh=refresh)
    rows = candidate_rows(path, names)
    passed, incomplete = apply_filters(rows, fundamentals.get("data", {}), filters)
    return {"as_of": fundamentals.get("as_of", ""), "evaluated": len(rows) - len(incomplete),
            "total": len(rows), "incomplete": len(incomplete), "matches": passed}


def describe(filters: Filters) -> str:
    """Human-readable filter summary, shown on the dashboard."""
    parts = []
    if filters.per_max is not None:
        parts.append(f"PER {filters.per_max:g}배 미만")
    if filters.pbr_max is not None:
        parts.append(f"PBR {filters.pbr_max:g}배 미만")
    if filters.dividend_min is not None:
        parts.append(f"배당수익률 {filters.dividend_min:g}% 이상")
    if filters.revenue_growth_min is not None:
        parts.append(f"매출성장률 {filters.revenue_growth_min:g}% 초과")
    if filters.operating_income_growth_min is not None:
        parts.append(f"영업이익성장률 {filters.operating_income_growth_min:g}% 초과")
    if filters.positive_free_cash_flow:
        parts.append("잉여현금흐름 양수")
    if filters.markets:
        parts.append("/".join(filters.markets))
    return " · ".join(parts) or "조건 없음"


def save_latest(result: dict, filters: Filters, path: Path = LATEST_RESULT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": result.get("as_of", ""), "conditions": describe(filters),
        "evaluated": result.get("evaluated"), "total": result.get("total"),
        "incomplete": result.get("incomplete"), "matches": result.get("matches", []),
    }, ensure_ascii=False), encoding="utf-8")
    return path


def format_table(result: dict) -> str:
    header = f"{'코드':<8}{'종목명':<16}{'시장':<8}{'PER':>7}{'PBR':>6}{'FCF(억)':>11}{'매출성장':>9}{'영업익성장':>11}{'기준분기':>9}"
    lines = [header]
    for row in result["matches"]:
        fcf = row.get("free_cash_flow")
        lines.append(
            f"{row['ticker']:<8}{str(row.get('name') or '')[:14]:<16}{str(row.get('market') or '-'):<8}"
            f"{row.get('per') or 0:>7.2f}{row.get('pbr') or 0:>6.2f}"
            f"{(fcf / 1e8 if fcf is not None else 0):>11.0f}"
            f"{row.get('revenue_growth_pct') or 0:>8.1f}%{row.get('operating_income_growth_pct') or 0:>10.1f}%"
            f"{row.get('period', ''):>9}"
        )
    return "\n".join(lines)


def _env_float(name: str, default: float | None) -> float | None:
    """Settings live in .env so the scheduled run can be retuned without
    editing run_stock_alarm.ps1; an empty value means "no such filter"."""
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return default


def main() -> None:
    from .app import load_env

    load_env()
    import os

    parser = argparse.ArgumentParser(description="Screen collected fundamentals (PER/PBR/growth/free cash flow)")
    parser.add_argument("--per-max", type=float, default=_env_float("SCREENER_PER_MAX", 15.0))
    parser.add_argument("--pbr-max", type=float, default=_env_float("SCREENER_PBR_MAX", None))
    parser.add_argument("--dividend-min", type=float, default=_env_float("SCREENER_DIVIDEND_MIN", None))
    parser.add_argument("--growth-min", type=float, default=_env_float("SCREENER_GROWTH_MIN", 5.0), help="minimum year-over-year revenue growth %%")
    parser.add_argument("--operating-growth-min", type=float, default=_env_float("SCREENER_OPERATING_GROWTH_MIN", None))
    parser.add_argument("--positive-fcf", action="store_true", default=os.environ.get("SCREENER_POSITIVE_FCF", "1") == "1")
    parser.add_argument("--any-fcf", dest="positive_fcf", action="store_false", help="do not require positive free cash flow")
    parser.add_argument("--market", default=os.environ.get("SCREENER_MARKETS", ""), help="KOSPI, KOSDAQ, or blank for both")
    parser.add_argument("--refresh", action="store_true", help="re-fetch KRX fundamentals instead of using today's cache")
    parser.add_argument("--db", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--out", type=Path, help="write matches to this CSV")
    args = parser.parse_args()
    filters = Filters(
        per_max=args.per_max, pbr_max=args.pbr_max, dividend_min=args.dividend_min,
        revenue_growth_min=args.growth_min, operating_income_growth_min=args.operating_growth_min,
        positive_free_cash_flow=args.positive_fcf,
        markets=tuple(value.strip().upper() for value in args.market.split(",") if value.strip()),
    )
    result = screen(filters, args.db, refresh=args.refresh)
    save_latest(result, filters)
    print(f"기준일 {result['as_of']} | 평가 {result['evaluated']}/{result['total']}종목 "
          f"(자료부족 {result['incomplete']}) | 조건 통과 {len(result['matches'])}종목")
    print(format_table(result))
    if args.out and result["matches"]:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(result["matches"][0]))
            writer.writeheader()
            writer.writerows(result["matches"])
        print(f"saved {args.out}")


if __name__ == "__main__":
    main()
