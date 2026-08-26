import os
import unittest
from unittest.mock import patch

from datetime import date, datetime

from stock_alarm.app import average_intraday_range_pct, average_true_range_pct, day_change_pct, evaluate_naver_candidate, expected_volume_fraction, passes_risk_filters, time_adjusted_volume_ratio


class RiskFiltersTest(unittest.TestCase):
    def test_day_change_pct(self):
        self.assertEqual(10, day_change_pct(100, 110))

    def test_average_intraday_range_pct(self):
        self.assertEqual(10, average_intraday_range_pct([110], [100], [100]))

    def test_rejects_large_day_change(self):
        old = os.environ.get("MAX_DAY_CHANGE_PCT")
        os.environ["MAX_DAY_CHANGE_PCT"] = "8"
        try:
            self.assertFalse(passes_risk_filters(100, 120, [121], [119], [120]))
        finally:
            if old is None:
                os.environ.pop("MAX_DAY_CHANGE_PCT", None)
            else:
                os.environ["MAX_DAY_CHANGE_PCT"] = old

    def test_intraday_volume_is_adjusted_for_elapsed_session(self):
        now = datetime(2026, 8, 10, 9, 30)
        adjusted, fraction = time_adjusted_volume_ratio(0.5, date(2026, 8, 10), now)
        self.assertLess(fraction, 0.5)
        self.assertGreater(adjusted, 1.0)

    def test_historical_volume_is_not_adjusted(self):
        adjusted, fraction = time_adjusted_volume_ratio(1.5, date(2026, 8, 7), datetime(2026, 8, 10, 9, 30))
        self.assertEqual((1.5, 1.0), (adjusted, fraction))

    def test_expected_volume_fraction_reaches_one_at_close(self):
        self.assertEqual(1.0, expected_volume_fraction(datetime(2026, 8, 10, 15, 30)))

    def test_average_true_range_includes_gaps(self):
        rows = [[20260807, 0, 102, 98, 100, 1], [20260810, 0, 112, 108, 110, 1]]
        self.assertAlmostEqual(12 / 110 * 100, average_true_range_pct(rows), places=6)

    @patch.dict("os.environ", {"MAX_ENTRY_DAY_CHANGE_PCT": "5", "MAX_MA20_DISTANCE_PCT": "10", "MAX_MA20_DISTANCE_ATR": "1.5"}, clear=True)
    @patch("stock_alarm.app.naver_rows")
    def test_new_entry_filters_preserve_legacy_shadow_result(self, naver_rows):
        naver_rows.return_value = [[20260701 + index, 0, 100, 100, 100, 100] for index in range(20)] + [[20260721, 0, 106, 100, 106, 200]]
        result = evaluate_naver_candidate("005930", "Samsung", date(2026, 7, 21), 0, 1.5)
        self.assertEqual(1, result.values["legacy_passed"])
        self.assertEqual(0, result.values["passed"])
        self.assertIn("entry_day_change", result.values["rejection_reasons"])
        self.assertIn("extended_above_ma20", result.values["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
