import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.app import is_trading_day, latest_naver_trading_day


class NaverDateTest(unittest.TestCase):
    @patch("stock_alarm.app.naver_rows")
    def test_latest_naver_trading_day_uses_last_row(self, rows):
        rows.return_value = [["20240722", 1, 1, 1, 1, 1, 0], ["20240724", 1, 1, 1, 1, 1, 0]]

        self.assertEqual(date(2024, 7, 24), latest_naver_trading_day())

    @patch("stock_alarm.app.latest_naver_trading_day", return_value=date(2026, 7, 27))
    def test_is_trading_day_true_when_latest_matches_today(self, _latest):
        self.assertTrue(is_trading_day(date(2026, 7, 27)))

    @patch("stock_alarm.app.latest_naver_trading_day", return_value=date(2026, 7, 24))
    def test_is_trading_day_false_on_weekend_or_holiday(self, _latest):
        self.assertFalse(is_trading_day(date(2026, 7, 26)))


if __name__ == "__main__":
    unittest.main()
