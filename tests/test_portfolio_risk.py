import os
import tempfile
import unittest
from datetime import datetime

from stock_alarm.portfolio_risk import new_buys_allowed, snapshot


class PortfolioRiskTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
