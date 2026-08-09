import os
import unittest
from datetime import date, datetime
from unittest.mock import patch

from stock_alarm.app import is_market_alert_time, is_trading_day, latest_naver_trading_day


class NaverDateTest(unittest.TestCase):
    @patch("stock_alarm.app.naver_rows")
    def test_latest_naver_trading_day_uses_last_row(self, rows):
        rows.return_value = [["20240722", 1, 1, 1, 1, 1, 0], ["20240724", 1, 1, 1, 1, 1, 0]]

        self.assertEqual(date(2024, 7, 24), latest_naver_trading_day())
        self.assertEqual(300, rows.call_args.kwargs["max_cache_age_seconds"])

    @patch.dict(os.environ, {"AS_OF_DATE": "2024-07-24"}, clear=True)
    @patch("stock_alarm.app.naver_rows", return_value=[["20240724", 1, 1, 1, 1, 1, 0]])
    def test_historical_trading_day_cache_does_not_expire(self, rows):
        self.assertEqual(date(2024, 7, 24), latest_naver_trading_day())
        self.assertIsNone(rows.call_args.kwargs["max_cache_age_seconds"])

    @patch("stock_alarm.app.latest_naver_trading_day", return_value=date(2026, 7, 27))
    def test_is_trading_day_true_when_latest_matches_today(self, _latest):
        self.assertTrue(is_trading_day(date(2026, 7, 27)))

    @patch("stock_alarm.app.latest_naver_trading_day", return_value=date(2026, 7, 24))
    def test_is_trading_day_false_on_weekend_or_holiday(self, _latest):
        self.assertFalse(is_trading_day(date(2026, 7, 26)))

    @patch("stock_alarm.app.is_trading_day", return_value=True)
    def test_is_market_alert_time_only_during_market_hours(self, _trading):
        self.assertTrue(is_market_alert_time(datetime(2026, 7, 27, 9, 0)))
        self.assertTrue(is_market_alert_time(datetime(2026, 7, 27, 15, 30)))
        self.assertFalse(is_market_alert_time(datetime(2026, 7, 27, 19, 15)))

    @patch("stock_alarm.app.is_trading_day")
    def test_is_market_alert_time_skips_trading_day_lookup_after_hours(self, trading_day):
        self.assertFalse(is_market_alert_time(datetime(2026, 7, 27, 19, 15)))
        trading_day.assert_not_called()


if __name__ == "__main__":
    unittest.main()
