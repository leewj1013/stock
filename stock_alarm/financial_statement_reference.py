from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date

from .dart_reference import corp_code_by_stock

CACHE_DIR = os.path.join(".cache", "dart", "financials")
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def _num(row: dict, key: str) -> float | None:
    raw = row.get(key, "")
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).replace(",", ""))
    except ValueError:
        return None


def _account_values(rows: list[dict], name_contains: str) -> tuple[float | None, float | None]:
    """Return (this-term, prior-term) amounts for the first matching account,
    preferring the consolidated statement (CFS) over the separate one (OFS)."""
    candidates = [row for row in rows if name_contains in row.get("account_nm", "")]
    if not candidates:
        return None, None
    consolidated = [row for row in candidates if row.get("fs_div") == "CFS"]
    row = (consolidated or candidates)[0]
    return _num(row, "thstrm_amount"), _num(row, "frmtrm_amount")


def _fetch_rows_for_year(corp_code: str, key: str, year: int, reprt_code: str) -> list[dict]:
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?" + urllib.parse.urlencode(
        {"crtfc_key": key, "corp_code": corp_code, "bsns_year": year, "reprt_code": reprt_code}
    )
    with urllib.request.urlopen(url, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("list") or []


# (month DART's filing deadline has passed, reprt_code, fiscal year offset from today.year).
# Ordered most-recent-first so the first one that's actually been filed wins.
# Quarterly/half-year filings report revenue and operating income as
# year-to-date cumulative figures, so comparing this YTD to the prior year's
# same-period YTD (frmtrm_amount) is still a like-for-like growth rate --
# ROE from a partial-year net income is understated relative to a full-year
# rate, but that's an acceptable trade for fresher data in a re-ranking
# signal, not a safety filter.
_QUARTERLY_REPORT_SEQUENCE = [
    (11, "11014", 0), (8, "11012", 0), (5, "11013", 0), (3, "11011", -1),
]


def _candidate_reports(today: date) -> list[tuple[int, str]]:
    for month_available, reprt_code, year_offset in _QUARTERLY_REPORT_SEQUENCE:
        if today.month >= month_available:
            year = today.year + year_offset
            return [(year, reprt_code), (year - 1, "11011")]
    return [(today.year - 1, "11011"), (today.year - 2, "11011")]


def fetch_account_summary(ticker: str, year: int | None = None, reprt_code: str = "11011") -> dict:
    """DART "단일회사 주요계정" (fnlttSinglAcnt) for the most recently filed
    report -- quarterly/half-year when its filing window has opened,
    otherwise the latest annual report. Passing `year` explicitly pins the
    lookup to that year/reprt_code (used by tests and one-off lookups)
    instead of the live waterfall.

    thstrm_amount/frmtrm_amount give this-period and prior-period figures in
    the same response, so a single call covers both the raw accounts and
    what's needed for a year-over-year growth rate.
    """
    key = os.environ.get("DART_API_KEY", "")
    corp_code = corp_code_by_stock(ticker)
    if not key or not corp_code:
        return {}
    candidates = [(year, reprt_code)] if year is not None else _candidate_reports(date.today())
    rows: list[dict] = []
    resolved_year, resolved_reprt_code = candidates[0]
    for candidate_year, candidate_reprt_code in candidates:
        rows = _fetch_rows_for_year(corp_code, key, candidate_year, candidate_reprt_code)
        resolved_year, resolved_reprt_code = candidate_year, candidate_reprt_code
        if rows:
            break
    if not rows:
        return {}
    revenue, revenue_prev = _account_values(rows, "매출액")
    operating_income, operating_income_prev = _account_values(rows, "영업이익")
    net_income, _ = _account_values(rows, "당기순이익")
    assets, _ = _account_values(rows, "자산총계")
    liabilities, _ = _account_values(rows, "부채총계")
    equity, _ = _account_values(rows, "자본총계")
    return {
        "revenue": revenue, "revenue_prev": revenue_prev,
        "operating_income": operating_income, "operating_income_prev": operating_income_prev,
        "net_income": net_income, "assets": assets, "liabilities": liabilities, "equity": equity,
        "bsns_year": resolved_year, "reprt_code": resolved_reprt_code,
    }


def _growth_pct(current: float | None, prior: float | None) -> float | None:
    if current is None or not prior:
        return None
    return (current - prior) / abs(prior) * 100


def _ratio_pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator * 100


# Quarterly/half-year filings report net income as a year-to-date cumulative
# figure, but equity (the ROE denominator) is a point-in-time balance-sheet
# number -- dividing YTD income by full equity understates ROE for most of
# the year (e.g. a Q1 filing only carries ~3 months of income). Annualizing
# the numerator makes ROE comparable across filing types; the margin ratios
# below divide two flow figures over the same YTD period so they don't need
# this adjustment.
_ANNUALIZATION_FACTOR = {"11011": 1.0, "11012": 2.0, "11013": 4.0, "11014": 12 / 9}


def _ratios_from_summary(summary: dict) -> dict:
    net_income = summary.get("net_income")
    factor = _ANNUALIZATION_FACTOR.get(summary.get("reprt_code"), 1.0)
    annualized_net_income = net_income * factor if net_income is not None else None
    return {
        "roe_pct": _ratio_pct(annualized_net_income, summary.get("equity")),
        "debt_ratio_pct": _ratio_pct(summary.get("liabilities"), summary.get("equity")),
        "operating_margin_pct": _ratio_pct(summary.get("operating_income"), summary.get("revenue")),
        "revenue_growth_pct": _growth_pct(summary.get("revenue"), summary.get("revenue_prev")),
        "operating_income_growth_pct": _growth_pct(summary.get("operating_income"), summary.get("operating_income_prev")),
    }


def financial_ratios(ticker: str, cache_dir: str = CACHE_DIR, ttl_seconds: int = CACHE_TTL_SECONDS) -> dict:
    """ROE/부채비율/영업이익률/성장률, cached per ticker for a week since these
    only change when a new quarterly/annual filing lands."""
    if os.environ.get("DART_FINANCIALS_LOOKUP", "0") != "1":
        return {}
    cache_path = os.path.join(cache_dir, f"{ticker}.json")
    if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) <= ttl_seconds:
        with open(cache_path, encoding="utf-8") as file:
            return json.load(file)
    try:
        summary = fetch_account_summary(ticker)
    except Exception:
        return {}
    ratios = _ratios_from_summary(summary) if summary else {}
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as file:
        json.dump(ratios, file, ensure_ascii=False)
    return ratios
