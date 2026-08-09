import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.market_summary import market_rows, message, run, summary


class MarketSummaryTest(unittest.TestCase):
    def test_summary(self):
        rows = [{"change_pct": "1.00"}, {"change_pct": "-2.00"}, {"change_pct": "0.00"}]
        self.assertEqual({"count": "3", "up_count": "1", "down_count": "1", "up_ratio_pct": "33.3", "avg_change_pct": "-0.33"}, summary(rows))

    def test_message(self):
        text = message([{"ticker": "A", "name": "Alpha", "change_pct": "1.23", "trading_value": "100"}, {"ticker": "B", "name": "Beta", "change_pct": "-2.34", "trading_value": "200"}])
        self.assertIn("[개장 전 시장 요약]", text)
        self.assertIn("기준 종목: 2개", text)
        self.assertIn("상승/하락: 1개 / 1개", text)
        self.assertIn("직전 거래일 거래대금 상위", text)

    @patch("stock_alarm.market_summary.stock_name", side_effect=lambda _ticker, fallback: fallback)
    @patch("stock_alarm.market_summary.configured_stocks", return_value={"005930": "삼성전자"})
    @patch("stock_alarm.market_summary.naver_rows", return_value=[[20260728, 0, 0, 0, 100, 10], [20260729, 0, 0, 0, 110, 20]])
    def test_market_rows(self, _rows, _stocks, _name):
        self.assertEqual([{"ticker": "005930", "name": "삼성전자", "change_pct": "10.00", "trading_value": "2200"}], market_rows(date(2026, 7, 29)))

    @patch("stock_alarm.market_summary.load_env")
    @patch("stock_alarm.market_summary.message", return_value="summary")
    @patch("stock_alarm.market_summary.send_notification", return_value="telegram")
    def test_run_sends_previous_session_summary_before_open(self, send, _message, _env):
        self.assertEqual("telegram", run())
        send.assert_called_once_with("summary")


if __name__ == "__main__":
    unittest.main()
