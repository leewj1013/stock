import unittest

from unittest.mock import patch

from stock_alarm.daily_summary import message


class DailySummaryTest(unittest.TestCase):
    @patch("stock_alarm.daily_summary.change_summary", return_value="change=+1.25p since previous")
    @patch("stock_alarm.daily_summary.position_count", return_value=2)
    @patch("stock_alarm.daily_summary.latest_position_summary", return_value="보유 평균 수익률: +2.50%")
    @patch("stock_alarm.daily_summary.tail_csv")
    def test_message(self, tail_csv, _summary, _count, _change):
        tail_csv.side_effect = [
            [{"ticker": "A", "name": "Alpha"}, {"ticker": "B", "name": "Beta"}],
            [],
        ]

        text = message()

        self.assertIn("[오늘의 주식 알림 요약]", text)
        self.assertIn("추천 후보: 2개", text)
        self.assertIn("매도 검토: 없음", text)
        self.assertIn("보유 종목: 2개", text)
        self.assertIn("직전 대비: +1.25p", text)


        self.assertIn("추천 TOP3:", text)
        self.assertIn("1. Alpha(A)", text)


if __name__ == "__main__":
    unittest.main()
