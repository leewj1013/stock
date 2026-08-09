import os
import unittest

from datetime import date, datetime

from stock_alarm.app import average_intraday_range_pct, average_true_range_pct, day_change_pct, expected_volume_fraction, passes_risk_filters, time_adjusted_volume_ratio


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


if __name__ == "__main__":
    unittest.main()
