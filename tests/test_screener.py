import json
import os
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

    def test_table_renders_matches(self):
        passed, _incomplete = apply_filters(ROWS, FUNDAMENTALS, BASE)
        table = format_table({"matches": passed})
        self.assertIn("싼성장주", table)
        self.assertIn("KOSPI", table)


if __name__ == "__main__":
    unittest.main()
