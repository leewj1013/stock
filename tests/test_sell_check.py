import csv
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.sell_check import SellAlert, check_position, format_message, read_positions, run, write_log


class SellCheckTest(unittest.TestCase):
    def test_read_positions(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            file.write("ticker,name,entry_price,entry_date\n005930,Samsung,80000,2026-07-25\n")
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))

        self.assertEqual("005930", read_positions(file.name)[0]["ticker"])

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows")
    def test_check_position_alerts_on_stop_loss(self, naver_rows, _name):
        naver_rows.return_value = [[20260701, 0, 0, 0, 100, 1]] * 19 + [[20260724, 0, 0, 0, 94, 1]]

        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24))

        self.assertEqual("005930", alert.ticker)
        self.assertIn("손절", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows")
    def test_check_position_alerts_on_return_drop(self, naver_rows, _name):
        naver_rows.return_value = [[20260701, 0, 0, 0, 100, 1]] * 20

        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24), 5)

        self.assertIn("직전 점검 대비 수익률 5.0%p 악화", alert.reason)

    def test_format_message(self):
        message = format_message([SellAlert("005930", "Samsung", 100, 94, -6.0, "손절 기준 -5.0% 이탈")])

        self.assertIn("매도 검토 알림", message)
        self.assertIn("005930", message)
        self.assertIn("-6.0%", message)

    def test_write_log(self):
        with tempfile.NamedTemporaryFile(delete=False) as file:
            path = file.name
        os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

        write_log([SellAlert("005930", "Samsung", 100, 94, -6.0, "손절")], path)

        with open(path, newline="", encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual("005930", rows[0]["ticker"])

    @patch("stock_alarm.sell_check.latest_naver_trading_day")
    @patch("stock_alarm.sell_check.find_alerts", return_value=[])
    @patch("stock_alarm.sell_check.read_positions", return_value=[])
    @patch("stock_alarm.sell_check.write_log")
    def test_run_skips_empty_alert_by_default(self, _write, _positions, _alerts, _day):
        self.assertEqual("no_alerts", run())


if __name__ == "__main__":
    unittest.main()
