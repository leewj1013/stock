import os
import unittest

from stock_alarm.app import average_intraday_range_pct, day_change_pct, passes_risk_filters


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


if __name__ == "__main__":
    unittest.main()
