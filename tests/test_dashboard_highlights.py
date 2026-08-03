import unittest
from unittest.mock import patch

from stock_alarm.dashboard import total_average_return


class DashboardHighlightsTest(unittest.TestCase):
    @patch("stock_alarm.dashboard.tail_csv")
    def test_total_average_return_uses_latest_sells_and_current_positions(self, tail_csv):
        def fake_tail(path, _count):
            if path.endswith("sell_alerts.csv"):
                return [
                    {"ticker": "A", "return_pct": "-10"},
                    {"ticker": "A", "return_pct": "-4"},
                    {"ticker": "B", "return_pct": "8"},
                ]
            if path.endswith("positions_report.csv"):
                return [
                    {"created_at": "old", "ticker": "C", "return_pct": "99"},
                    {"created_at": "now", "ticker": "C", "return_pct": "2"},
                ]
            return []

        tail_csv.side_effect = fake_tail

        self.assertEqual("2.00", total_average_return())


if __name__ == "__main__":
    unittest.main()
