import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.app import latest_naver_trading_day


class NaverDateTest(unittest.TestCase):
    @patch("stock_alarm.app.naver_rows")
    def test_latest_naver_trading_day_uses_last_row(self, rows):
        rows.return_value = [["20240722", 1, 1, 1, 1, 1, 0], ["20240724", 1, 1, 1, 1, 1, 0]]

        self.assertEqual(date(2024, 7, 24), latest_naver_trading_day())


if __name__ == "__main__":
    unittest.main()
