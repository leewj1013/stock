import unittest
from unittest.mock import patch

from stock_alarm.dashboard import display_value, position_summary_rows


class DashboardHighlightsTest(unittest.TestCase):
    def test_display_value_translates_operational_status(self):
        self.assertEqual("보유", display_value("HOLD"))
        self.assertEqual("텔레그램 정상 전송: 2026-08-09T15:35:00", display_value("telegram ok at 2026-08-09T15:35:00"))

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

    @patch("stock_alarm.dashboard.recent_position_checks")
    @patch("stock_alarm.dashboard.latest_position_rows")
    def test_position_summary_matches_by_position_id_not_just_ticker(self, positions, checks):
        # A ticker re-recommended after a prior position was already sold
        # gets a NEW position_id. Its own HOLD decision must not be shadowed
        # by the old, closed position's ALREADY_ALERTED decision just
        # because they share a ticker.
        positions.return_value = [{"ticker": "161890", "position_id": "new-position", "name": "Korea Kolmar", "return_pct": "21.76"}]
        checks.return_value = [
            {"ticker": "161890", "position_id": "old-position", "decision": "ALREADY_ALERTED", "holding_days": None, "reasons": "sell_alert_after_entry"},
            {"ticker": "161890", "position_id": "new-position", "decision": "HOLD", "holding_days": 15, "reasons": ""},
        ]

        rows = position_summary_rows()

        self.assertEqual(1, len(rows))
        self.assertEqual("HOLD", rows[0]["decision"])


if __name__ == "__main__":
    unittest.main()
