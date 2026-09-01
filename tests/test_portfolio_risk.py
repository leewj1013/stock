import os
import tempfile
import unittest
from datetime import datetime

from stock_alarm.portfolio_risk import evaluate_risk_state, new_buys_allowed, snapshot


class PortfolioRiskTest(unittest.TestCase):
    def test_pure_evaluator_covers_all_four_live_triggers(self):
        with unittest.mock.patch.dict(os.environ, {
            "RISK_DAILY_LOSS_PCT": "2", "RISK_WEEKLY_LOSS_PCT": "5",
            "RISK_MAX_DRAWDOWN_PCT": "10", "RISK_MAX_EXPOSURE_PCT": "70",
        }):
            result = evaluate_risk_state(
                {"total_equity": 85_000, "holdings_value": 70_000},
                daily_start_equity=100_000, weekly_start_equity=100_000,
                high_water=100_000,
            )
        self.assertEqual(
            {"daily_loss_limit", "weekly_loss_limit", "drawdown_limit", "exposure_limit"},
            set(result["reason"].split(",")),
        )

    def test_daily_loss_halts_only_new_buys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            snapshot({"total_equity": 100_000, "holdings_value": 40_000}, path, datetime(2026, 8, 27, 9, 0))
            result = snapshot({"total_equity": 97_000, "holdings_value": 40_000}, path, datetime(2026, 8, 27, 15, 30))
            self.assertEqual("halted", result["status"])
            self.assertIn("daily_loss_limit", result["reason"])
            self.assertFalse(new_buys_allowed(path)[0])

    def test_exposure_limit_halts_new_buys_without_selling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            result = snapshot({"total_equity": 100_000, "holdings_value": 80_000}, path, datetime(2026, 8, 27, 9, 0))
            self.assertEqual("halted", result["status"])
            self.assertIn("exposure_limit", result["reason"])

    def test_exposure_limit_override_uses_tighter_profile_cap(self):
        # 60% exposure passes the 70% env default but must halt a profile
        # (e.g. risk-neutral) configured with a tighter 50% cap.
        with unittest.mock.patch.dict(os.environ, {"RISK_MAX_EXPOSURE_PCT": "70"}):
            default_result = evaluate_risk_state(
                {"total_equity": 100_000, "holdings_value": 60_000}, 100_000, 100_000, 100_000,
            )
            overridden_result = evaluate_risk_state(
                {"total_equity": 100_000, "holdings_value": 60_000}, 100_000, 100_000, 100_000,
                exposure_limit_pct=50,
            )
        self.assertNotIn("exposure_limit", default_result["reason"])
        self.assertIn("exposure_limit", overridden_result["reason"])

    def test_snapshot_passes_exposure_limit_override_through(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            result = snapshot({"total_equity": 100_000, "holdings_value": 60_000}, path, datetime(2026, 8, 27, 9, 0), exposure_limit_pct=50)
            self.assertEqual("halted", result["status"])
            self.assertIn("exposure_limit", result["reason"])

    def test_risk_state_reports_resume_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            snapshot({"total_equity": 100_000, "holdings_value": 80_000}, path, datetime(2026, 8, 27, 9, 0))
            result = snapshot({"total_equity": 100_000, "holdings_value": 40_000}, path, datetime(2026, 8, 28, 9, 0))
            self.assertEqual("active", result["status"])
            self.assertEqual("resumed", result["transition"])


if __name__ == "__main__":
    unittest.main()
