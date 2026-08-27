import unittest
from datetime import datetime as real_datetime
from unittest.mock import patch

from stock_alarm.daily_summary import latest_recommendations, message, run


class DailySummaryTest(unittest.TestCase):
    @patch("stock_alarm.daily_summary.virtual_deposits_since", return_value=0)
    @patch("stock_alarm.daily_summary.previous_virtual_valuation", return_value={"equity": 9900000})
    @patch("stock_alarm.daily_summary.virtual_trader_state")
    @patch("stock_alarm.daily_summary.current_prices", return_value={"A": 11000})
    @patch("stock_alarm.daily_summary.recent_virtual_sales", return_value=[])
    @patch("stock_alarm.daily_summary.recent_virtual_trades")
    @patch("stock_alarm.daily_summary.datetime")
    @patch("stock_alarm.daily_summary.tail_csv")
    def test_message(self, tail_csv, datetime, trades, _sales, _prices, state, _previous, _deposits):
        datetime.now.return_value = real_datetime(2026, 7, 31, 16, 0)
        trades.return_value = [{"created_at": "2026-07-31T09:10:00", "ticker": "A", "name": "Alpha", "quantity": 2, "allocation_pct": 20}]
        state.return_value = {
            "total_equity": 10_000_000, "total_return_pct": 1.25, "cash": 8_000_000,
            "holdings_value": 2_000_000, "holdings_return_pct": 2.5,
            "holdings": [{"ticker": "A", "name": "Alpha", "return_pct": 2.5}],
        }
        def fake_tail(path, _count):
            if path.endswith("recommendations.csv"):
                return [{"created_at": "2026-07-31T09:00:00", "ticker": "A", "name": "Alpha"}, {"created_at": "2026-07-31T09:00:00", "ticker": "B", "name": "Beta"}]
            return []

        tail_csv.side_effect = fake_tail
        text = message()
        self.assertIn("[주식 마감 브리핑 | 07/31]", text)
        self.assertIn("추천 2종목 · 가상매수 1종목 · 가상매도 0종목", text)
        self.assertIn("오늘 손익 100,000원 (+1.01%)", text)
        self.assertIn("현금 8,000,000원 · 주식 2,000,000원", text)
        self.assertIn("최고 Alpha +2.50%", text)
        self.assertIn("매수: Alpha 2주 · 비중 20%", text)

    @patch("stock_alarm.daily_summary.tail_csv")
    @patch("stock_alarm.daily_summary.datetime")
    def test_latest_recommendations_uses_all_today_batches_and_dedupes(self, datetime, tail_csv):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-31"
        def fake_tail(path, _count):
            if path.endswith("recommendations.csv"):
                return [{"created_at": "2026-07-30T15:00:00", "ticker": "N"}, {"created_at": "2026-07-31T09:00:00", "ticker": "N"}, {"created_at": "2026-07-31T10:00:00", "ticker": "K"}, {"created_at": "2026-07-31T11:00:00", "ticker": "N"}]
            return []

        tail_csv.side_effect = fake_tail
        self.assertEqual(["K", "N"], [row["ticker"] for row in latest_recommendations()])

    @patch("stock_alarm.daily_summary.load_env")
    @patch("stock_alarm.daily_summary.is_trading_day", return_value=False)
    @patch("stock_alarm.daily_summary.send_notification")
    def test_run_skips_when_market_closed(self, send, _trading, _env):
        self.assertEqual("market_closed", run())
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
