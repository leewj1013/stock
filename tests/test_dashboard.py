import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.dashboard import e, performance_summary_rows, recommendation_rank_rows, render, sell_alerted_recommendation_rows, table, write


class DashboardTest(unittest.TestCase):
    def test_escape(self):
        self.assertEqual("&lt;x&gt;", e("<x>"))

    def test_table(self):
        html = table("T", [{"a": "1"}], ["a"])

        self.assertIn("<table>", html)
        self.assertIn("<td>1</td>", html)

    @patch("stock_alarm.dashboard.tail_csv")
    def test_performance_summary_rows(self, tail_csv):
        tail_csv.return_value = [
            {"metric": "avg_1d_return_pct", "value": "1.23"},
            {"metric": "win_rate_1d_pct", "value": "60.0"},
            {"metric": "suggested_min_score", "value": "70"},
        ]

        rows = performance_summary_rows()

        self.assertIn({"metric": "전체 1일 평균", "value": "1.23%"}, rows)
        self.assertIn({"metric": "전체 1일 승률", "value": "60.0%"}, rows)
        self.assertIn({"metric": "추천 최소점수 제안", "value": "70"}, rows)

    @patch("stock_alarm.dashboard.tail_csv")
    def test_recommendation_rank_rows(self, tail_csv):
        tail_csv.return_value = [
            {"ticker": "000001", "name": "A", "return_1d_pct": "1.00"},
            {"ticker": "000001", "name": "A", "return_1d_pct": "-2.00"},
            {"ticker": "000002", "name": "B", "return_1d_pct": "3.00"},
            {"ticker": "000003", "name": "C", "return_1d_pct": ""},
        ]

        rows = recommendation_rank_rows()

        self.assertEqual("000002", rows[0]["ticker"])
        self.assertEqual("1", rows[0]["picks"])
        self.assertEqual("3.00", rows[0]["avg_1d_return_pct"])
        self.assertEqual("50.0", rows[1]["win_rate_1d_pct"])

        self.assertEqual("000001", recommendation_rank_rows(worst=True)[0]["ticker"])

    @patch("stock_alarm.dashboard.tail_csv")
    def test_sell_alerted_recommendation_rows(self, tail_csv):
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"ticker": "000001", "name": "A", "score": "80", "entry_close": "1000", "return_1d_pct": "-1.00"}]
            return [{"ticker": "000001", "name": "A", "return_pct": "-5.00", "reason": "stop loss"}]

        tail_csv.side_effect = fake_tail

        rows = sell_alerted_recommendation_rows()

        self.assertEqual(
            {
                "ticker": "000001",
                "name": "A",
                "score": "80",
                "entry_close": "1000",
                "return_1d_pct": "-1.00",
                "sell_return_pct": "-5.00",
                "sell_reason": "stop loss",
            },
            rows[0],
        )

    @patch("stock_alarm.dashboard.daily_check_lines", return_value=["daily ok"])
    @patch("stock_alarm.dashboard.metric_cards", return_value=[("positions", "1")])
    @patch("stock_alarm.dashboard.performance_summary_rows", return_value=[])
    @patch("stock_alarm.dashboard.recommendation_rank_rows", return_value=[])
    @patch("stock_alarm.dashboard.sell_alerted_recommendation_rows", return_value=[])
    @patch("stock_alarm.dashboard.tail_text", return_value=[])
    @patch("stock_alarm.dashboard.tail_csv", return_value=[])
    def test_render(self, _csv, _text, _sell, _rank, _summary, _cards, _daily):
        html = render()

        self.assertIn("stockAlarm Dashboard", html)
        self.assertIn("Daily check", html)
        self.assertIn("Recommendation stats", html)
        self.assertIn("Top recommendation performance", html)
        self.assertIn("Worst recommendation performance", html)
        self.assertIn("Recommendations with sell alerts", html)

    @patch("stock_alarm.dashboard.render", return_value="<html></html>")
    def test_write(self, _render):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "dashboard.html")

            self.assertEqual(path, write(path))
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
