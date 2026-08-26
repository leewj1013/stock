import unittest
from datetime import datetime
from unittest.mock import patch

from stock_alarm.run_gate import should_run


class RunGateTest(unittest.TestCase):
    def test_intraday_skips_weekend_without_market_lookup(self):
        with patch("stock_alarm.run_gate.is_trading_day") as market:
            market.return_value = False

            self.assertFalse(should_run("intraday", datetime(2026, 8, 1, 11, 45)))

    @patch("stock_alarm.run_gate.is_trading_day", return_value=True)
    def test_intraday_runs_during_market_time(self, _market):
        self.assertTrue(should_run("intraday", datetime(2026, 7, 31, 9, 1)))

    @patch("stock_alarm.run_gate.is_trading_day", return_value=True)
    def test_sell_runs_independently_during_market_time(self, _market):
        self.assertTrue(should_run("sell", datetime(2026, 7, 31, 9, 1)))

    def test_intraday_window_includes_preopen_and_final_close_refresh(self):
        with patch("stock_alarm.run_gate.is_trading_day", return_value=True):
            self.assertTrue(should_run("intraday", datetime(2026, 7, 31, 8, 50)))
            self.assertTrue(should_run("intraday", datetime(2026, 7, 31, 15, 40)))
            self.assertFalse(should_run("intraday", datetime(2026, 7, 31, 15, 41)))

    @patch("stock_alarm.run_gate.is_trading_day")
    def test_open_skips_weekend_before_trading_day_lookup(self, trading_day):
        self.assertFalse(should_run("open", datetime(2026, 8, 1, 8, 30)))
        trading_day.assert_not_called()

    @patch("stock_alarm.run_gate.is_trading_day")
    def test_open_runs_on_weekday_without_waiting_for_same_day_close(self, trading_day):
        self.assertTrue(should_run("open", datetime(2026, 8, 3, 8, 30)))
        trading_day.assert_not_called()


if __name__ == "__main__":
    unittest.main()
