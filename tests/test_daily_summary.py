import unittest
from unittest.mock import patch

from stock_alarm.daily_summary import latest_recommendations, message, run


class DailySummaryTest(unittest.TestCase):
    @patch("stock_alarm.daily_summary.change_summary", return_value="change=+1.25p since previous")
    @patch("stock_alarm.daily_summary.active_position_count", return_value=2)
    @patch("stock_alarm.daily_summary.latest_position_summary", return_value="보유 평균 수익률: +2.50%")
    @patch("stock_alarm.daily_summary.datetime")
    @patch("stock_alarm.daily_summary.tail_csv")
    def test_message(self, tail_csv, datetime, _summary, _count, _change):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-31"
        tail_csv.side_effect = [
            [{"created_at": "2026-07-31T09:00:00", "ticker": "A", "name": "Alpha"}, {"created_at": "2026-07-31T09:00:00", "ticker": "B", "name": "Beta"}],
            [],
        ]

        text = message()

        self.assertIn("[오늘 주식 알림 요약]", text)
        self.assertIn("추천 후보: 2개", text)
        self.assertIn("매도 검토: 없음", text)
        self.assertIn("보유 종목: 2개", text)
        self.assertIn("직전 대비 +1.25p", text)
        self.assertIn("추천 TOP3:", text)
        self.assertIn("1. Alpha(A)", text)

    @patch("stock_alarm.daily_summary.tail_csv")
    @patch("stock_alarm.daily_summary.datetime")
    def test_latest_recommendations_uses_today_latest_batch_and_dedupes(self, datetime, tail_csv):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-31"
        tail_csv.return_value = [
            {"created_at": "2026-07-30T15:00:00", "ticker": "N", "name": "NAVER"},
            {"created_at": "2026-07-31T09:00:00", "ticker": "N", "name": "NAVER"},
            {"created_at": "2026-07-31T09:00:00", "ticker": "K", "name": "카카오"},
            {"created_at": "2026-07-31T09:00:00", "ticker": "N", "name": "NAVER"},
        ]

        rows = latest_recommendations()

        self.assertEqual(["K", "N"], [row["ticker"] for row in rows])

    @patch("stock_alarm.daily_summary.load_env")
    @patch("stock_alarm.daily_summary.is_trading_day", return_value=False)
    @patch("stock_alarm.daily_summary.send_notification")
    def test_run_skips_when_market_closed(self, send, _trading, _env):
        self.assertEqual("market_closed", run())
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
