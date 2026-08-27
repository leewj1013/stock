import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from stock_alarm.data_store import upsert_recommendation_outcomes
from stock_alarm.strategy_learning import DEFAULT_WEIGHTS, adjusted_score, learn, objective


class StrategyLearningTest(unittest.TestCase):
    def test_objective_prefers_excess_return(self):
        row = {"return_1d_pct": 5, "excess_1d_pct": 2}
        self.assertEqual(2, objective(row))

    def test_learning_waits_for_minimum_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            self.assertEqual("insufficient_data", learn(path)["status"])

    def test_daily_weight_change_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            rows = []
            start = datetime(2026, 1, 1)
            for index in range(120):
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
            with patch.dict(os.environ, {"LEARNING_MIN_SAMPLES": "100", "LEARNING_MAX_DAILY_WEIGHT_CHANGE": "0.05"}):
                result = learn(path, datetime(2026, 8, 27, 16, 0))
            self.assertIn(result["status"], {"promoted", "rejected"})
            self.assertLessEqual(abs(result["weights"]["volume_score"] - 1.0), 0.0501)
            self.assertEqual(60.0, adjusted_score({"volume_score": 60}, path))


if __name__ == "__main__":
    unittest.main()
