import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.market_summary import alpha_vantage_daily, market_regime, market_rows, message, run, summary


class MarketSummaryTest(unittest.TestCase):
    def test_summary(self):
        rows = [{"change_pct": "1.00"}, {"change_pct": "-2.00"}, {"change_pct": "0.00"}]
        self.assertEqual({"count": "3", "up_count": "1", "down_count": "1", "up_ratio_pct": "33.3", "avg_change_pct": "-0.33"}, summary(rows))

    def test_message(self):
        text = message([{"ticker": "A", "name": "Alpha", "change_pct": "1.23", "trading_value": "100"}, {"ticker": "B", "name": "Beta", "change_pct": "-2.34", "trading_value": "200"}], [{"symbol": "SPY", "name": "S&P 500", "change_pct": "1.10"}])
        self.assertIn("[08:30 오늘의 매매 브리핑]", text)
        self.assertIn("미국 증시 마감", text)
        self.assertIn("S&P 500(SPY): +1.10%", text)
        self.assertIn("상승/하락: 1개 / 1개", text)
        self.assertIn("거래대금 주도 종목", text)
        self.assertIn("권장 신규 매수 한도", text)

    def test_market_regime_combines_domestic_and_us_market(self):
        result = market_regime({"avg_change_pct": "2.0", "up_ratio_pct": "80.0"}, [{"change_pct": "1.5"}])
        self.assertEqual("🟢 공격", result["label"])
        self.assertEqual("70%", result["buy_limit"])

    @patch("stock_alarm.market_summary.urllib.request.urlopen")
    def test_alpha_vantage_daily_uses_latest_two_sessions(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"Time Series (Daily)":{"2026-08-21":{"4. close":"110"},"2026-08-20":{"4. close":"100"}}}'
        row = alpha_vantage_daily("SPY", "secret")
        self.assertEqual("10.00", row["change_pct"])
        self.assertNotIn("secret", str(row))

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
