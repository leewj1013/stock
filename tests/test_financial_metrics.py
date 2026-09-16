import sqlite3
import unittest
from contextlib import closing

from stock_alarm.financial_metrics import metrics, quarterly_metrics, trailing_twelve_months
from stock_alarm.financial_statement_lines import SCHEMA

# (year, reprt_code, sj_div, account_id, account_nm, thstrm_amount, thstrm_add_amount)
# Income statement: thstrm is the three-month figure, add is year-to-date.
# Cash flow: thstrm is year-to-date, with no add column.
LINES = [
    (2025, "11013", "IS", "ifrs-full_Revenue", "매출액", 100.0, 100.0),
    (2025, "11013", "IS", "dart_OperatingIncomeLoss", "영업이익", 10.0, 10.0),
    (2025, "11013", "CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 40.0, None),
    (2025, "11012", "IS", "ifrs-full_Revenue", "매출액", 150.0, 250.0),
    (2025, "11012", "IS", "dart_OperatingIncomeLoss", "영업이익", 20.0, 30.0),
    (2025, "11012", "CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 90.0, None),
    (2026, "11013", "IS", "ifrs-full_Revenue", "매출액", 150.0, 150.0),
    (2026, "11013", "IS", "dart_OperatingIncomeLoss", "영업이익", 20.0, 20.0),
]


def build_db(extra=()):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.executemany(
        "INSERT INTO financial_statement_lines(ticker,bsns_year,reprt_code,fs_div,sj_div,account_id,account_nm,account_detail,"
        "thstrm_amount,thstrm_add_amount,frmtrm_amount,frmtrm_q_amount,bfefrmtrm_amount,ord,currency,collected_at) "
        "VALUES('A',?,?,'CFS',?,?,?,'',?,?,NULL,NULL,NULL,1,'KRW','now')",
        list(LINES) + list(extra),
    )
    return db


class FinancialMetricsTest(unittest.TestCase):
    def test_income_statement_quarter_is_used_as_filed_not_differenced(self):
        with closing(build_db()) as db:
            rows = {row["period"]: row for row in quarterly_metrics(db, "A")}

        # 2Q filing reports 150 for the quarter and 250 year-to-date; the
        # quarter figure is already three months, so it must not be differenced.
        self.assertEqual(100.0, rows["2025Q1"]["revenue"])
        self.assertEqual(150.0, rows["2025Q2"]["revenue"])
        self.assertEqual(20.0, rows["2025Q2"]["operating_income"])

    def test_cash_flow_is_cumulative_so_the_quarter_is_differenced(self):
        with closing(build_db()) as db:
            rows = {row["period"]: row for row in quarterly_metrics(db, "A")}

        self.assertEqual(40.0, rows["2025Q1"]["operating_cash_flow"])
        self.assertEqual(50.0, rows["2025Q2"]["operating_cash_flow"])

    def test_annual_income_statement_q4_is_the_year_minus_year_to_date(self):
        annual = [
            (2025, "11014", "IS", "ifrs-full_Revenue", "매출액", 120.0, 370.0),
            (2025, "11011", "IS", "ifrs-full_Revenue", "매출액", 500.0, None),
        ]
        with closing(build_db(annual)) as db:
            rows = {row["period"]: row for row in quarterly_metrics(db, "A")}

        self.assertEqual(120.0, rows["2025Q3"]["revenue"])
        self.assertEqual(130.0, rows["2025Q4"]["revenue"])  # 500 full year - 370 through Q3

    def test_growth_compares_against_the_same_quarter_last_year(self):
        with closing(build_db()) as db:
            rows = {row["period"]: row for row in quarterly_metrics(db, "A")}

        self.assertAlmostEqual(50.0, rows["2026Q1"]["revenue_growth_pct"])
        self.assertAlmostEqual(100.0, rows["2026Q1"]["operating_income_growth_pct"])
        self.assertIsNone(rows["2025Q1"]["revenue_growth_pct"])

    def test_margins_and_ebitda_need_their_inputs(self):
        row = metrics({"revenue": 200.0, "cost_of_sales": 150.0, "operating_income": 20.0, "depreciation": 5.0,
                       "amortisation": 3.0, "operating_cash_flow": 30.0, "capex": 12.0})
        self.assertEqual(50.0, row["gross_profit"])
        self.assertAlmostEqual(25.0, row["gross_margin_pct"])
        self.assertEqual(28.0, row["ebitda"])
        self.assertAlmostEqual(14.0, row["ebitda_margin_pct"])
        self.assertEqual(18.0, row["free_cash_flow"])
        self.assertIsNone(metrics({"revenue": 200.0, "operating_income": 20.0})["ebitda"])

    def test_ttm_requires_four_complete_quarters(self):
        rows = [{"period": f"2025Q{quarter}", "revenue": 100.0, "operating_income": None, "assets": 10.0,
                 "liabilities": 6.0, "cash": 1.0, "pretax_income": 20.0, "income_tax": 5.0} for quarter in range(1, 5)]
        ttm = trailing_twelve_months(rows)
        self.assertEqual(400.0, ttm["revenue"])
        self.assertIsNone(ttm["operating_income"])
        self.assertEqual(5.0, ttm["net_debt"])
        self.assertAlmostEqual(25.0, ttm["effective_tax_rate_pct"])


if __name__ == "__main__":
    unittest.main()
