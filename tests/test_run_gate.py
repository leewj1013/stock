import unittest
from datetime import datetime
from unittest.mock import patch

from stock_alarm.run_gate import should_run


class RunGateTest(unittest.TestCase):
    def test_intraday_skips_weekend_without_market_lookup(self):
        with patch("stock_alarm.run_gate.is_market_alert_time") as market:
            market.return_value = False

            self.assertFalse(should_run("intraday", datetime(2026, 8, 1, 11, 45)))

    @patch("stock_alarm.run_gate.is_market_alert_time", return_value=True)
    def test_intraday_runs_during_market_time(self, _market):
        self.assertTrue(should_run("intraday", datetime(2026, 7, 31, 9, 1)))

    @patch("stock_alarm.run_gate.is_trading_day")
    def test_open_skips_weekend_before_trading_day_lookup(self, trading_day):
        self.assertFalse(should_run("open", datetime(2026, 8, 1, 8, 30)))
        trading_day.assert_not_called()


if __name__ == "__main__":
    unittest.main()
