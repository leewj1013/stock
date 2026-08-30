import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from stock_alarm.data_store import upsert_recommendation_outcomes
from stock_alarm.strategy_learning import DEFAULT_WEIGHTS, adjusted_score, decision_message, learn, objective, return_distribution_p_value


class StrategyLearningTest(unittest.TestCase):
    def test_objective_prefers_excess_return(self):
        row = {"return_1d_pct": 5, "excess_1d_pct": 2}
        self.assertEqual(2, objective(row))

    def test_learning_waits_for_minimum_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            result = learn(path)
            self.assertEqual("insufficient_data", result["status"])
            self.assertGreaterEqual(result["minimum"], 300)

    def test_return_distribution_test_detects_clear_improvement(self):
        self.assertLess(return_distribution_p_value([5.0] * 40, [-1.0] * 40), 0.05)

    def test_decision_message_includes_samples_fold_results_and_p_value(self):
        message = decision_message({
            "status": "rejected", "sample_count": 300, "p_value": 0.08,
            "decision_reason": "aggregate_significance_failed",
            "folds": [{"fold": 1, "baseline_return": 1, "proposed_return": 2, "proposed_mdd": -1, "p_value": 0.04, "passed": True}],
        })
        self.assertIn("표본: 300건", message)
        self.assertIn("구간1", message)
        self.assertIn("통합 p-value: 0.0800", message)

    def test_learning_rejects_when_significance_is_insufficient(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            rows = []
            start = datetime(2026, 1, 1)
            for index in range(360):
                factors = {factor: 1.0 for factor in DEFAULT_WEIGHTS}
                factors["volume_score"] = float(index % 30)
                rows.append({
                    "pick_date": (start + timedelta(days=index)).date().isoformat(), "ticker": f"S{index:03d}",
                    "name": "Test", "strategy_version": "test", "score": 50,
                    "factors_json": json.dumps(factors), "return_1d_pct": float(index % 7),
                    "excess_1d_pct": float(index % 7), "quality_status": "valid", "updated_at": "now",
                })
            upsert_recommendation_outcomes(rows, path)
            with patch("stock_alarm.strategy_learning.return_distribution_p_value", return_value=0.5):
                result = learn(path, datetime(2026, 8, 27, 16, 0))
            self.assertEqual("rejected", result["status"])
            self.assertEqual(3, len(result["folds"]))
            self.assertIn("significance", result["decision_reason"])

    def test_daily_weight_change_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            rows = []
            start = datetime(2026, 1, 1)
            for index in range(360):
                value = (index % 20) - 10
                factors = {factor: 1.0 for factor in DEFAULT_WEIGHTS}
                factors["volume_score"] = float(index % 20)
                rows.append({
                    "pick_date": (start + timedelta(days=index)).date().isoformat(), "ticker": f"T{index:03d}",
                    "name": "Test", "strategy_version": "test", "score": 50,
                    "factors_json": json.dumps(factors), "return_1d_pct": value,
                    "excess_1d_pct": value, "quality_status": "valid", "updated_at": "now",
                })
            upsert_recommendation_outcomes(rows, path)
            with patch.dict(os.environ, {"LEARNING_MIN_SAMPLES": "300", "LEARNING_VALIDATION_MIN_SAMPLES": "60", "LEARNING_VALIDATION_FOLDS": "3", "LEARNING_MAX_DAILY_WEIGHT_CHANGE": "0.05"}):
                result = learn(path, datetime(2026, 8, 27, 16, 0))
            self.assertIn(result["status"], {"promoted", "rejected"})
            self.assertLessEqual(abs(result["weights"]["volume_score"] - 1.0), 0.0501)
            self.assertEqual(60.0, adjusted_score({"volume_score": 60}, path))


if __name__ == "__main__":
    unittest.main()
