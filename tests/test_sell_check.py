import csv
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

from stock_alarm.sell_check import SellAlert, alert_summary, alerted_tickers, check_position, find_alerts, format_message, max_returns, previous_returns, read_positions, run, write_log


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
        self.assertIn("손절 기준", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 0, 0, 100, 1]] * 20)
    def test_check_position_alerts_on_return_drop(self, _rows, _name):
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24), 5)
        self.assertIn("직전 평가 대비 수익률 5.0%p 악화", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 0, 0, 102, 1]] * 20)
    def test_check_position_alerts_on_profit_giveback(self, _rows, _name):
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24), max_return=8)
        self.assertIn("고점 수익률 8.0% 대비 6.0%p 반납", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 102, 98, 100, 1]] * 20)
    def test_check_position_applies_time_stop(self, _rows, _name):
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100", "entry_date": "2026-07-01"}, date(2026, 7, 24))
        self.assertIn("보유 후 기대수익 미달", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows")
    def test_atr_can_widen_fixed_stop(self, naver_rows, _name):
        naver_rows.return_value = [[20260701, 0, 110, 90, 100, 1]] * 19 + [[20260724, 0, 110, 90, 94, 1]]
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24))
        self.assertIsNone(alert)

    def test_returns_are_scoped_by_position_id(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["created_at", "position_id", "ticker", "return_pct"])
            writer.writerow(["now", "first", "005930", "8"])
            writer.writerow(["later", "first", "005930", "2"])
            writer.writerow(["later", "second", "005930", "10"])
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        self.assertEqual(8.0, max_returns(file.name)["first"])
        self.assertEqual(10.0, max_returns(file.name)["second"])
        self.assertEqual(2.0, previous_returns(file.name)["first"])

    def test_alerted_tickers(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["created_at", "ticker", "name"])
            writer.writerow(["2026-07-25", "005930", "Samsung"])
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        self.assertEqual({"005930"}, alerted_tickers(file.name))

    @patch("stock_alarm.sell_check.latest_sell_alert_times", return_value={"005930": datetime(2026, 7, 25)})
    @patch("stock_alarm.sell_check.check_position")
    def test_find_alerts_skips_position_sold_after_entry(self, check_position, _times):
        result = find_alerts([{"ticker": "005930", "name": "Samsung", "entry_price": "100", "entry_date": "2026-07-24"}], date(2026, 7, 24))
        self.assertEqual([], result)
        check_position.assert_not_called()

    @patch("stock_alarm.sell_check.latest_sell_alert_times", return_value={"005930": datetime(2026, 7, 25)})
    @patch("stock_alarm.sell_check.check_position")
    def test_find_alerts_checks_reentry_after_old_sell(self, check_position, _times):
        check_position.return_value = None
        find_alerts([{"ticker": "005930", "name": "Samsung", "entry_price": "100", "entry_date": "2026-07-26"}], date(2026, 7, 27))
        check_position.assert_called_once()

    def test_format_message_and_summary(self):
        alert = SellAlert("005930", "Samsung", 100, 94, -6.0, "손절 기준 -5.0% 이탈")
        message = format_message([alert])
        self.assertIn("매도 검토 알림", message)
        self.assertIn("매도 검토 요약: 손실 -6.0%", message)
        self.assertEqual("고점 대비 수익 반납", alert_summary(SellAlert("A", "A", 100, 102, 2, "고점 대비 반납")))
        self.assertEqual("20일선 이탈", alert_summary(SellAlert("A", "A", 100, 101, 1, "20일선 이탈")))

    def test_write_log(self):
        with tempfile.NamedTemporaryFile(delete=False) as file:
            path = file.name
        os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        write_log([SellAlert("005930", "Samsung", 100, 94, -6.0, "손절")], path)
        with open(path, newline="", encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual("손실 -6.0%", rows[0]["summary"])

    @patch("stock_alarm.sell_check.latest_naver_trading_day", return_value=date(2026, 7, 24))
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.sell_check.is_market_alert_time", return_value=True)
    @patch("stock_alarm.sell_check.find_alerts", return_value=[])
    @patch("stock_alarm.sell_check.read_positions", return_value=[])
    @patch("stock_alarm.sell_check.write_log")
    def test_run_skips_empty_alert_by_default(self, _write, _positions, _alerts, _trading, _start, _finish, _day):
        self.assertEqual("no_alerts", run())

    @patch("stock_alarm.sell_check.is_market_alert_time", return_value=False)
    def test_run_skips_when_market_closed(self, _trading):
        self.assertEqual("market_closed", run())


if __name__ == "__main__":
    unittest.main()
