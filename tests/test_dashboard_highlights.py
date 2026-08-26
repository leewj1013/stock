import unittest
from unittest.mock import patch

from stock_alarm.dashboard import card_class, display_value, position_summary_rows, primary_metric_cards, total_average_return


class DashboardHighlightsTest(unittest.TestCase):
    def test_primary_cards_use_the_same_rows_as_visible_tables(self):
        recommendations = [{"ticker": "A"}, {"ticker": "B"}]
        sells = [{"ticker": "C"}]
        positions = [{"ticker": "A", "return_pct": "2.0"}, {"ticker": "B", "return_pct": "-1.0"}]

        with patch("stock_alarm.dashboard.tail_csv", return_value=[]), patch("stock_alarm.dashboard.today_run_summary", return_value="2/2 ok"), patch("stock_alarm.dashboard.latest_virtual_valuation", return_value={}):
            cards = dict(primary_metric_cards(recommendations, sells, positions))

        self.assertEqual("2", cards["today recommendations"])
        self.assertEqual("1", cards["today sell alerts"])
        self.assertEqual("2", cards["positions"])
        self.assertEqual("0.50%", cards["average position return"])

    def test_display_value_translates_operational_status(self):
        self.assertEqual("보유", display_value("HOLD"))
        self.assertEqual("텔레그램 정상 전송: 2026-08-09T15:35:00", display_value("telegram ok at 2026-08-09T15:35:00"))

    def test_partial_run_card_is_not_green(self):
        self.assertEqual("bad", card_class("today runs", "1/2 ok"))
        self.assertEqual("ok", card_class("today runs", "2/2 ok"))

    @patch("stock_alarm.dashboard.active_position_count", return_value=1)
    @patch("stock_alarm.dashboard.today_sell_alert_rows", return_value=[])
    @patch("stock_alarm.dashboard.today_recommendation_rows", return_value=[{"ticker": "A"}])
    @patch("stock_alarm.dashboard.today_run_summary", return_value="3/3 ok")
    @patch("stock_alarm.dashboard.latest_position_rows", return_value=[{"ticker": "A", "return_pct": "2.5"}])
    @patch("stock_alarm.dashboard.latest_virtual_valuation", return_value={"return_pct": -0.24, "return_change_pct": -0.10})
    @patch("stock_alarm.dashboard.tail_csv", return_value=[{"created_at": "2026-08-09T15:35:00", "channel": "telegram"}])
    def test_primary_metric_cards_are_limited_to_operating_summary(self, _tail, _virtual, _positions, _runs, _recommendations, _sells, _count):
        cards = dict(primary_metric_cards())

        self.assertEqual(8, len(cards))
        self.assertEqual("2.50%", cards["average position return"])
        self.assertEqual("-0.24%", cards["가상 트레이더 수익률"])
        self.assertEqual("-0.10%p", cards["직전 배치 대비"])
        self.assertIn("telegram ok", cards["last telegram"])

    @patch("stock_alarm.dashboard.recent_position_checks")
    @patch("stock_alarm.dashboard.latest_position_rows")
    def test_position_summary_combines_latest_sell_evaluation(self, positions, checks):
        positions.return_value = [{"ticker": "A", "name": "Alpha", "return_pct": "1.2"}]
        checks.return_value = [
            {"ticker": "A", "holding_days": 4, "decision": "HOLD", "reasons": "정상", "dynamic_stop_loss_pct": -5.0},
            {"ticker": "A", "holding_days": 3, "decision": "OLD", "reasons": "과거값", "dynamic_stop_loss_pct": -4.0},
        ]

        row = position_summary_rows()[0]

        self.assertEqual(4, row["holding_days"])
        self.assertEqual("HOLD", row["decision"])
        self.assertEqual(-5.0, row["dynamic_stop_loss_pct"])

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
