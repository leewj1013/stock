import unittest

from stock_alarm.position_constraint_diagnosis import concentration, constraint_summary


class PositionConstraintDiagnosisTest(unittest.TestCase):
    def test_concentration_uses_portfolio_weights(self):
        result = concentration([{"position_weights_json": '{"A": 0.3, "B": 0.2}'}])
        self.assertAlmostEqual(0.13, result["average_hhi"])
        self.assertEqual(30.0, result["max_position_pct"])

    def test_constraint_summary_separates_market_modes(self):
        states = [{"correlation_limited_count": 2, "correlation_rejected_count": 1,
                   "correlation_reduced_pct": 10, "market_exposure_limit_pct": 70}]
        result = constraint_summary(states, [])
        self.assertEqual(2, result["limited_in_aggressive"])
        self.assertEqual(0, result["limited_in_defensive"])


if __name__ == "__main__":
    unittest.main()
