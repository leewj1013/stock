import unittest

from stock_alarm.market_filter_risk_revalidation import raw_reasons, trigger_transition_events


class MarketFilterRiskRevalidationTest(unittest.TestCase):
    def test_raw_reasons_uses_live_evaluator_field(self):
        self.assertEqual({"daily_loss_limit", "drawdown_limit"}, raw_reasons({"risk_raw_reason": "daily_loss_limit,drawdown_limit"}))

    def test_trigger_events_detect_entry_and_release_without_recalculating_risk(self):
        states = [
            {"date": "2022-01-01", "risk_raw_reason": "", "equity": 100, "cash_weight": .5, "drawdown_pct": 0, "exposure_pct": 50},
            {"date": "2022-01-02", "risk_raw_reason": "daily_loss_limit", "equity": 97, "cash_weight": .5, "drawdown_pct": -3, "exposure_pct": 50},
            {"date": "2022-01-03", "risk_raw_reason": "", "equity": 98, "cash_weight": .5, "drawdown_pct": -2, "exposure_pct": 50},
        ]
        events = trigger_transition_events(states)
        self.assertEqual(["entered", "released"], [event["event"] for event in events])
        self.assertEqual("daily_loss_limit", events[0]["trigger"])


if __name__ == "__main__":
    unittest.main()
