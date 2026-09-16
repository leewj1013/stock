"""Derive quarterly fundamentals from the stored DART statement lines.

DART reports the three statement types differently, and mixing them up
silently produces nonsense totals:

* income statement (IS/CIS), quarterly report: `thstrm_amount` is already the
  three-month figure; `thstrm_add_amount` is the year-to-date total.
* income statement, annual report (11011): `thstrm_amount` is the full year,
  so Q4 = full year - the 3Q report's year-to-date total.
* cash flow (CF): always cumulative, so every quarter is differenced from the
  previous report of the same business year.
* balance sheet (BS): point-in-time, used as filed.

Accounts are matched by IFRS/DART account_id first (names vary between filers:
"매출액", "수익(매출액)", "영업수익" are all ifrs-full_Revenue) with a name
fallback for filers that leave the standard code blank.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from contextlib import closing
from pathlib import Path

from .point_in_time_store import DEFAULT_PATH

QUARTER_OF = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
ANNUAL_CODE = "11011"
IS, CF, BS = "is", "cf", "bs"
# item -> (account_ids, name fragments, statement kind)
ACCOUNTS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "revenue": (("ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers", "dart_OperatingRevenue"), ("매출액", "영업수익", "수익(매출액)"), IS),
    "cost_of_sales": (("ifrs-full_CostOfSales",), ("매출원가",), IS),
    "gross_profit": (("ifrs-full_GrossProfit",), ("매출총이익",), IS),
    "operating_income": (("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"), ("영업이익", "영업손익"), IS),
    "net_income": (("ifrs-full_ProfitLoss",), ("당기순이익",), IS),
    "pretax_income": (("ifrs-full_ProfitLossBeforeTax",), ("법인세비용차감전",), IS),
    "income_tax": (("ifrs-full_IncomeTaxExpenseContinuingOperations",), ("법인세비용",), IS),
    "operating_cash_flow": (("ifrs-full_CashFlowsFromUsedInOperatingActivities",), ("영업활동",), CF),
    "capex": (("ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",), ("유형자산의 취득",), CF),
    "intangible_capex": (("ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",), ("무형자산의 취득",), CF),
    # Filers label depreciation half a dozen ways ("감가상각비", "감가상각비에 대한
    # 조정", "사용권자산감가상각비"...), so these two are matched by pattern in
    # _depreciation_rows() rather than by a single account id.
    "depreciation": ((), (), CF),
    "amortisation": ((), (), CF),
    "assets": (("ifrs-full_Assets",), ("자산총계",), BS),
    "liabilities": (("ifrs-full_Liabilities",), ("부채총계",), BS),
    "equity": (("ifrs-full_Equity",), ("자본총계",), BS),
    "cash": (("ifrs-full_CashAndCashEquivalents",), ("현금및현금성자산",), BS),
}
FLOW_ITEMS = [item for item, (_ids, _names, kind) in ACCOUNTS.items() if kind in (IS, CF)]


# "상각후원가금융자산" is amortised-cost financial assets, not depreciation --
# matching it would put a balance-sheet asset into the D&A line.
DEPRECIATION_PATTERNS = ("감가상각", "사용권자산상각", "투자부동산상각")
AMORTISATION_PATTERNS = ("무형자산상각",)
NOT_DEPRECIATION = ("상각후원가",)


def _depreciation_rows(rows: list[dict], patterns: tuple[str, ...]) -> list[dict]:
    """One row per account id, so a filer listing several depreciation lines
    (plant, right-of-use, investment property) is summed but never counted twice."""
    seen: dict[str, dict] = {}
    for row in rows:
        name = row.get("account_nm") or ""
        if any(bad in name for bad in NOT_DEPRECIATION):
            continue
        if any(pattern in name for pattern in patterns) and row.get("thstrm_amount") is not None:
            seen.setdefault(row.get("account_id") or name, row)
    return list(seen.values())


def _matching_rows(rows: list[dict], item: str) -> list[dict]:
    if item == "depreciation":
        return _depreciation_rows(rows, DEPRECIATION_PATTERNS)
    if item == "amortisation":
        return _depreciation_rows(rows, AMORTISATION_PATTERNS)
    ids, names, _kind = ACCOUNTS[item]
    for wanted in ids:
        matches = [row for row in rows if row["account_id"] == wanted]
        if matches:
            return matches
    for fragment in names:
        matches = [row for row in rows if fragment in row["account_nm"]]
        if matches:
            return matches
    return []


def _amounts(rows: list[dict], item: str, reprt_code: str) -> tuple[float | None, float | None]:
    """(this quarter, year-to-date) as filed, before any differencing."""
    _ids, _names, kind = ACCOUNTS[item]
    matches = _matching_rows(rows, item)
    if not matches:
        return None, None
    quarter_parts = [row["thstrm_amount"] for row in matches if row["thstrm_amount"] is not None]
    if not quarter_parts:
        return None, None
    # Depreciation/amortisation come as several separate lines that add up;
    # every other item takes the first matching account.
    multi = item in ("depreciation", "amortisation")
    value = sum(quarter_parts) if multi else quarter_parts[0]
    if kind == BS:
        return value, None
    if reprt_code == ANNUAL_CODE or kind == CF:
        return None, value  # annual IS and every CF figure are cumulative
    cumulative_parts = [row["thstrm_add_amount"] for row in matches if row["thstrm_add_amount"] is not None]
    cumulative = (sum(cumulative_parts) if multi else cumulative_parts[0]) if cumulative_parts else value
    return value, cumulative


def filed_values(db, ticker: str) -> dict[tuple[int, int], dict[str, tuple[float | None, float | None]]]:
    rows = [dict(row) for row in db.execute(
        "SELECT bsns_year, reprt_code, account_id, account_nm, thstrm_amount, thstrm_add_amount "
        "FROM financial_statement_lines WHERE ticker=?",
        (ticker,),
    )]
    grouped: dict[tuple[int, int], list[dict]] = {}
    codes: dict[tuple[int, int], str] = {}
    for row in rows:
        quarter = QUARTER_OF.get(row["reprt_code"])
        if quarter:
            key = (int(row["bsns_year"]), quarter)
            grouped.setdefault(key, []).append(row)
            codes[key] = row["reprt_code"]
    return {key: {item: _amounts(value, item, codes[key]) for item in ACCOUNTS} for key, value in grouped.items()}


def quarterly_values(filed: dict[tuple[int, int], dict[str, tuple[float | None, float | None]]]) -> dict[tuple[int, int], dict[str, float | None]]:
    """Single-quarter values, differencing only what is filed cumulatively."""
    out: dict[tuple[int, int], dict[str, float | None]] = {}
    for (year, quarter), values in filed.items():
        row: dict[str, float | None] = {}
        previous = filed.get((year, quarter - 1)) if quarter > 1 else None
        for item, (_ids, _names, kind) in ACCOUNTS.items():
            this_quarter, cumulative = values[item]
            if kind == BS:
                row[item] = this_quarter
            elif this_quarter is not None:
                row[item] = this_quarter
            elif cumulative is None:
                row[item] = None
            elif quarter == 1:
                row[item] = cumulative
            else:
                prior_cumulative = previous[item][1] if previous else None
                row[item] = cumulative - prior_cumulative if prior_cumulative is not None else None
        out[(year, quarter)] = row
    return out


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator * 100


def metrics(row: dict[str, float | None]) -> dict[str, float | None]:
    revenue = row.get("revenue")
    gross = row.get("gross_profit")
    if gross is None and revenue is not None and row.get("cost_of_sales") is not None:
        gross = revenue - row["cost_of_sales"]
    da_parts = [row.get("depreciation"), row.get("amortisation")]
    da = sum(part for part in da_parts if part is not None) if any(part is not None for part in da_parts) else None
    operating = row.get("operating_income")
    ebitda = operating + da if operating is not None and da is not None else None
    capex_parts = [row.get("capex"), row.get("intangible_capex")]
    capex = sum(part for part in capex_parts if part is not None) if any(part is not None for part in capex_parts) else None
    ocf = row.get("operating_cash_flow")
    return {
        "revenue": revenue, "gross_profit": gross, "gross_margin_pct": _ratio(gross, revenue),
        "operating_income": operating, "operating_margin_pct": _ratio(operating, revenue),
        "net_income": row.get("net_income"), "operating_cash_flow": ocf,
        "ocf_to_operating_income_pct": _ratio(ocf, operating),
        "depreciation_amortisation": da, "ebitda": ebitda, "ebitda_margin_pct": _ratio(ebitda, revenue),
        "capex": capex, "free_cash_flow": ocf - capex if ocf is not None and capex is not None else None,
        "income_tax": row.get("income_tax"), "pretax_income": row.get("pretax_income"),
        "assets": row.get("assets"), "liabilities": row.get("liabilities"), "equity": row.get("equity"), "cash": row.get("cash"),
    }


def quarterly_metrics(db, ticker: str) -> list[dict]:
    """Single-quarter metrics, oldest first, with year-over-year growth."""
    values = quarterly_values(filed_values(db, ticker))
    computed = {key: metrics(row) for key, row in values.items()}
    out = []
    for (year, quarter) in sorted(computed):
        row = dict(computed[(year, quarter)])
        previous = computed.get((year - 1, quarter))
        for item, label in (("revenue", "revenue_growth_pct"), ("operating_income", "operating_income_growth_pct")):
            current, prior = row.get(item), (previous or {}).get(item)
            row[label] = (current - prior) / abs(prior) * 100 if current is not None and prior else None
        out.append({"ticker": ticker, "period": f"{year}Q{quarter}", "year": year, "quarter": quarter, **row})
    return out


def trailing_twelve_months(rows: list[dict]) -> dict[str, float | None]:
    """Sum of the last four complete quarters -- the DCF starting point."""
    recent = rows[-4:]
    out: dict[str, float | None] = {"quarters": len(recent), "period": f"{recent[0]['period']}~{recent[-1]['period']}" if recent else ""}
    for item in ("revenue", "operating_income", "net_income", "ebitda", "operating_cash_flow", "capex", "free_cash_flow", "income_tax", "pretax_income"):
        parts = [row.get(item) for row in recent]
        out[item] = sum(parts) if len(parts) == 4 and all(part is not None for part in parts) else None
    latest = recent[-1] if recent else {}
    for item in ("assets", "liabilities", "equity", "cash"):
        out[item] = latest.get(item)
    out["net_debt"] = (latest["liabilities"] - latest["cash"]) if latest.get("liabilities") is not None and latest.get("cash") is not None else None
    out["effective_tax_rate_pct"] = _ratio(out["income_tax"], out["pretax_income"])
    out["nopat"] = (out["operating_income"] * (1 - out["effective_tax_rate_pct"] / 100)
                    if out["operating_income"] is not None and out["effective_tax_rate_pct"] is not None else None)
    return out


def all_tickers(db) -> list[str]:
    return [row[0] for row in db.execute("SELECT DISTINCT ticker FROM financial_statement_lines ORDER BY ticker")]


def build(path: Path = DEFAULT_PATH, out_dir: Path = Path("reports/fundamentals")) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    quarterly_rows, ttm_rows = [], []
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        for ticker in all_tickers(db):
            rows = quarterly_metrics(db, ticker)
            quarterly_rows.extend(rows)
            ttm_rows.append({"ticker": ticker, **trailing_twelve_months(rows)})
    for name, rows in (("quarterly_metrics.csv", quarterly_rows), ("ttm_and_dcf_inputs.csv", ttm_rows)):
        with (out_dir / name).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return {"tickers": len(ttm_rows), "quarter_rows": len(quarterly_rows), "out_dir": str(out_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive quarterly fundamentals from stored DART statement lines")
    parser.add_argument("--db", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--out", type=Path, default=Path("reports/fundamentals"))
    args = parser.parse_args()
    print(build(args.db, args.out))


if __name__ == "__main__":
    main()
