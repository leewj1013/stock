import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.dashboard import e, performance_summary_rows, render, table, write


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

    @patch("stock_alarm.dashboard.daily_check_lines", return_value=["daily ok"])
    @patch("stock_alarm.dashboard.metric_cards", return_value=[("positions", "1")])
    @patch("stock_alarm.dashboard.performance_summary_rows", return_value=[])
    @patch("stock_alarm.dashboard.tail_text", return_value=[])
    @patch("stock_alarm.dashboard.tail_csv", return_value=[])
    def test_render(self, _csv, _text, _summary, _cards, _daily):
        html = render()

        self.assertIn("stockAlarm Dashboard", html)
        self.assertIn("Daily check", html)
        self.assertIn("Recommendation stats", html)

    @patch("stock_alarm.dashboard.render", return_value="<html></html>")
    def test_write(self, _render):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "dashboard.html")

            self.assertEqual(path, write(path))
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
