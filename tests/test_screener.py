import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from stock_alarm.screener import Filters, apply_filters, format_table, has_priced_rows, load_market_fundamentals

ROWS = [
    {"ticker": "000001", "name": "싼성장주", "period": "2026Q2", "revenue_growth_pct": 12.0,
     "operating_income_growth_pct": 30.0, "free_cash_flow": 5_000_000_000.0},
    {"ticker": "000002", "name": "비싼주", "period": "2026Q2", "revenue_growth_pct": 20.0,
     "operating_income_growth_pct": 40.0, "free_cash_flow": 1_000_000_000.0},
    {"ticker": "000003", "name": "현금유출주", "period": "2026Q2", "revenue_growth_pct": 30.0,
     "operating_income_growth_pct": 50.0, "free_cash_flow": -2_000_000_000.0},
    {"ticker": "000004", "name": "저성장주", "period": "2026Q2", "revenue_growth_pct": 1.0,
     "operating_income_growth_pct": 2.0, "free_cash_flow": 3_000_000_000.0},
    {"ticker": "000005", "name": "적자주", "period": "2026Q2", "revenue_growth_pct": 15.0,
     "operating_income_growth_pct": 10.0, "free_cash_flow": 1_000_000_000.0},
    {"ticker": "000006", "name": "PER없음", "period": "2026Q2", "revenue_growth_pct": 15.0,
     "operating_income_growth_pct": 10.0, "free_cash_flow": 1_000_000_000.0},
]
FUNDAMENTALS = {
    "000001": {"market": "KOSPI", "per": 8.0, "pbr": 0.9, "eps": 100.0, "dividend_yield": 3.0},
    "000002": {"market": "KOSPI", "per": 40.0, "pbr": 5.0, "eps": 100.0, "dividend_yield": 0.0},
    "000003": {"market": "KOSDAQ", "per": 10.0, "pbr": 1.2, "eps": 100.0, "dividend_yield": 0.0},
    "000004": {"market": "KOSDAQ", "per": 7.0, "pbr": 0.8, "eps": 100.0, "dividend_yield": 1.0},
    "000005": {"market": "KOSPI", "per": 0.0, "pbr": 0.5, "eps": 0.0, "dividend_yield": 0.0},
}
BASE = Filters(per_max=15.0, revenue_growth_min=5.0, positive_free_cash_flow=True)


class ScreenerTest(unittest.TestCase):
    def test_each_condition_rejects_its_own_case(self):
        passed, _incomplete = apply_filters(ROWS, FUNDAMENTALS, BASE)
        self.assertEqual(["000001"], [row["ticker"] for row in passed])

    def test_loss_making_zero_per_is_not_treated_as_cheap(self):
        passed, _incomplete = apply_filters(ROWS, FUNDAMENTALS, BASE)
        self.assertNotIn("000005", [row["ticker"] for row in passed])

    def test_missing_input_is_reported_not_silently_dropped(self):
        _passed, incomplete = apply_filters(ROWS, FUNDAMENTALS, BASE)
        self.assertEqual(["000006"], [row["ticker"] for row in incomplete])
        self.assertEqual("per", incomplete[0]["missing"])

    def test_market_filter_and_sorting_by_per(self):
        filters = Filters(per_max=15.0, markets=("KOSDAQ",))
        passed, _incomplete = apply_filters(ROWS, FUNDAMENTALS, filters)
        self.assertEqual(["000004", "000003"], [row["ticker"] for row in passed])

    def test_filters_are_optional(self):
        passed, incomplete = apply_filters(ROWS, FUNDAMENTALS, Filters(per_min=None))
        self.assertEqual(6, len(passed) + len(incomplete))

    def test_cache_written_today_is_reused_without_a_krx_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "krx.json"
            path.write_text(json.dumps({"as_of": "20260916", "data": FUNDAMENTALS}), encoding="utf-8")
            with patch("pykrx.stock.get_market_fundamental_by_ticker", side_effect=AssertionError("must not fetch")):
                self.assertEqual("20260916", load_market_fundamentals(cache_path=path)["as_of"])

    def test_yesterdays_cache_is_not_reused_for_an_undated_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "krx.json"
            path.write_text(json.dumps({"as_of": "20260915", "data": {}}), encoding="utf-8")
            stale = time.time() - 24 * 60 * 60
            os.utime(path, (stale, stale))
            with patch("stock_alarm.screener.date") as fake_date:
                fake_date.today.return_value = date.today()
                fake_date.fromtimestamp.return_value = date.today() - timedelta(days=1)
                with patch("pykrx.stock.get_market_fundamental_by_ticker", side_effect=RuntimeError("fetch attempted")):
                    with self.assertRaises(RuntimeError):
                        load_market_fundamentals(cache_path=path)

    def test_env_settings_supply_defaults_and_blank_disables_a_filter(self):
        from stock_alarm.screener import _env_float

        self.assertEqual(15.0, _env_float("SCREENER_MISSING", 15.0))
        with patch.dict(os.environ, {"SCREENER_PER_MAX": "12.5"}):
            self.assertEqual(12.5, _env_float("SCREENER_PER_MAX", 15.0))
        with patch.dict(os.environ, {"SCREENER_PER_MAX": ""}):
            self.assertIsNone(_env_float("SCREENER_PER_MAX", 15.0))
        with patch.dict(os.environ, {"SCREENER_PER_MAX": "not-a-number"}):
            self.assertEqual(15.0, _env_float("SCREENER_PER_MAX", 15.0))

    def test_all_zero_ratios_are_not_accepted_as_a_trading_day(self):
        # KRX answers with every ticker and all-zero ratios before the session
        # settles; screening on that would match nothing at all.
        self.assertFalse(has_priced_rows({"A": {"per": 0.0}, "B": {"per": 0.0}}))
        self.assertTrue(has_priced_rows({"A": {"per": 8.0}, "B": {"per": 0.0}}))
        self.assertFalse(has_priced_rows({}))

    def test_fundamentals_for_joins_cached_prices_with_stored_statements(self):
        from contextlib import closing as closing_db

        from stock_alarm.financial_statement_lines import SCHEMA
        from stock_alarm.screener import fundamentals_for

        with tempfile.TemporaryDirectory() as directory:
            pit = Path(directory) / "pit.sqlite3"
            with closing_db(sqlite3.connect(pit)) as db:
                db.executescript(SCHEMA)
                rows = [
                    (2025, "11013", "IS", "ifrs-full_Revenue", "매출액", 100.0, 100.0),
                    (2025, "11013", "IS", "dart_OperatingIncomeLoss", "영업이익", 20.0, 20.0),
                    (2026, "11013", "IS", "ifrs-full_Revenue", "매출액", 150.0, 150.0),
                    (2026, "11013", "IS", "dart_OperatingIncomeLoss", "영업이익", 30.0, 30.0),
                ]
                db.executemany(
                    "INSERT INTO financial_statement_lines(ticker,bsns_year,reprt_code,fs_div,sj_div,account_id,"
                    "account_nm,account_detail,thstrm_amount,thstrm_add_amount,frmtrm_amount,frmtrm_q_amount,"
                    "bfefrmtrm_amount,ord,currency,collected_at) VALUES('000100',?,?,'CFS',?,?,?,'',?,?,"
                    "NULL,NULL,NULL,1,'KRW','now')",
                    rows,
                )
                db.commit()
            cache = Path(directory) / "krx.json"
            cache.write_text(json.dumps({"as_of": "20260916", "data": {
                "000100": {"market": "KOSPI", "per": 9.5, "pbr": 0.8, "eps": 100.0, "dividend_yield": 2.0}}}), encoding="utf-8")
            with patch("stock_alarm.screener.FUNDAMENTAL_CACHE", cache):
                result = fundamentals_for(["000100", "999999"], path=pit)

        self.assertEqual(9.5, result["000100"]["per"])
        self.assertEqual("KOSPI", result["000100"]["market"])
        self.assertAlmostEqual(50.0, result["000100"]["revenue_growth_pct"])
        self.assertAlmostEqual(20.0, result["000100"]["operating_margin_pct"])
        self.assertEqual("2026Q1", result["000100"]["period"])
        # a ticker with neither prices nor filings still gets a row of blanks
        self.assertIsNone(result["999999"]["per"])
        self.assertIsNone(result["999999"]["free_cash_flow"])

    def test_previous_result_is_kept_across_days_but_not_overwritten_within_a_day(self):
        from stock_alarm.screener import new_matches, save_latest

        with tempfile.TemporaryDirectory() as directory:
            latest, previous = Path(directory) / "latest.json", Path(directory) / "previous.json"
            latest.write_text(json.dumps({"generated_at": "2026-09-17T16:02:00", "matches": [{"ticker": "A"}]}), encoding="utf-8")

            save_latest({"matches": [{"ticker": "A"}, {"ticker": "B", "name": "Beta"}]}, BASE, path=latest, previous=previous)
            self.assertEqual([{"ticker": "A"}], json.loads(previous.read_text(encoding="utf-8"))["matches"])
            added, total = new_matches(latest, previous)
            self.assertEqual((["B"], 2), ([row["ticker"] for row in added], total))

            # a second run today must keep yesterday's list as the comparison point
            save_latest({"matches": [{"ticker": "C"}]}, BASE, path=latest, previous=previous)
            self.assertEqual([{"ticker": "A"}], json.loads(previous.read_text(encoding="utf-8"))["matches"])

    def test_new_matches_without_history_reports_none_as_new(self):
        from stock_alarm.screener import new_matches

        with tempfile.TemporaryDirectory() as directory:
            latest = Path(directory) / "latest.json"
            latest.write_text(json.dumps({"matches": [{"ticker": "A"}]}), encoding="utf-8")
            self.assertEqual(([], 1), new_matches(latest, Path(directory) / "missing.json"))

    def test_table_renders_matches(self):
        passed, _incomplete = apply_filters(ROWS, FUNDAMENTALS, BASE)
        table = format_table({"matches": passed})
        self.assertIn("싼성장주", table)
        self.assertIn("KOSPI", table)


if __name__ == "__main__":
    unittest.main()
