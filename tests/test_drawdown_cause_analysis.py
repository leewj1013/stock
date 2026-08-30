import unittest

from stock_alarm.drawdown_cause_analysis import define_drawdown_window, loss_contributions


class DrawdownCauseAnalysisTest(unittest.TestCase):
    def test_window_uses_peak_before_claimed_date_and_finds_actual_drawdown_trigger(self):
        states = [
            {"date": "2022-08-08", "equity": "100", "drawdown_pct": "0", "risk_reason": "", "position_values_json": "{}"},
            {"date": "2022-08-09", "equity": "110", "drawdown_pct": "0", "risk_reason": "", "position_values_json": "{}"},
            {"date": "2022-12-28", "equity": "100", "drawdown_pct": "-9.09", "risk_reason": "exposure_limit", "position_values_json": "{}"},
            {"date": "2022-12-29", "equity": "98", "drawdown_pct": "-10.91", "risk_reason": "drawdown_limit", "position_values_json": "{}"},
        ]
        result = define_drawdown_window(states)
        self.assertEqual("2022-08-09", result["peak_date"])
        self.assertEqual("2022-12-29", result["actual_drawdown_trigger_date"])

    def test_contribution_identity_matches_equity_change(self):
        states = [
            {"date": "2022-01-01", "equity": "1000", "position_values_json": '{"A": 400}'},
            {"date": "2022-01-02", "equity": "960", "position_values_json": '{"A": 270, "B": 200}'},
        ]
        events = [
            {"date": "2022-01-02", "event": "sell_full", "ticker": "A", "name": "A", "gross_notional": "100", "transaction_cost": "10"},
            {"date": "2022-01-02", "event": "buy", "ticker": "B", "name": "B", "gross_notional": "200", "transaction_cost": "0"},
        ]
        rows, check = loss_contributions(states, events, "2022-01-01", "2022-01-02")
        self.assertAlmostEqual(-40, sum(row["pnl_contribution"] for row in rows))
        self.assertAlmostEqual(0, check["residual"])


if __name__ == "__main__":
    unittest.main()
