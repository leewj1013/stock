import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from stock_alarm.data_store import query_rows, upsert_recommendation_outcomes
from stock_alarm.strategy_learning import DEFAULT_WEIGHTS, adjusted_score, cliffs_delta, decision_message, learn, objective, return_distribution_p_value, run, sync_outcomes


class StrategyLearningTest(unittest.TestCase):
    @patch("stock_alarm.notifier.send_notification")
    @patch("stock_alarm.strategy_learning.learn", return_value={"status": "insufficient_data", "sample_count": 0})
    @patch("stock_alarm.strategy_learning.sync_outcomes")
    @patch("stock_alarm.strategy_learning.load_env")
    def test_run_loads_env_before_sending_the_notification(self, load_env, _sync, _learn, _send):
        # run() is invoked as its own `python -m` subprocess in the daily batch,
        # so TELEGRAM_BOT_TOKEN/CHAT_ID only exist if load_env() reads .env itself.
        run()
        load_env.assert_called_once()

    @patch("stock_alarm.app.naver_rows", return_value=[])
    @patch("stock_alarm.strategy_learning.query_rows", return_value=[])
    def test_sync_outcomes_caps_benchmark_lookup_to_today(self, _query_rows, naver_rows):
        # naver_rows caches its result forever, so a still-future end date would
        # freeze the benchmark lookup on a partial window -- same bug as the one
        # fixed in backtest.naver_close_after.
        today = datetime.now().date()
        pick_date = (today - timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            performance_path = os.path.join(directory, "performance.csv")
            with open(performance_path, "w", newline="", encoding="utf-8") as file:
                file.write("pick_date,ticker,name,return_1d_pct,return_3d_pct,return_5d_pct,return_10d_pct,return_20d_pct\n")
                file.write(f"{pick_date},005930,Samsung,1.0,,,,\n")

            sync_outcomes(performance_path=performance_path, path=os.path.join(directory, "test.db"))

        called_end = naver_rows.call_args.args[2]
        self.assertEqual(today, called_end)

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

    def test_cliffs_delta_is_plus_one_when_proposed_always_beats_baseline(self):
        self.assertEqual(1.0, cliffs_delta([5.0, 6.0], [1.0, 2.0]))

    def test_cliffs_delta_is_near_zero_when_distributions_fully_overlap(self):
        self.assertEqual(0.0, cliffs_delta([1.0, 2.0], [1.0, 2.0]))

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
                    "excess_1d_pct": float(index % 7), "return_20d_pct": float(index % 7),
                    "quality_status": "valid", "updated_at": "now",
                })
            upsert_recommendation_outcomes(rows, path)
            with patch("stock_alarm.strategy_learning.return_distribution_p_value", return_value=0.5):
                result = learn(path, datetime(2026, 8, 27, 16, 0))
            self.assertEqual("rejected", result["status"])
            self.assertEqual(3, len(result["folds"]))
            self.assertIn("significance", result["decision_reason"])

    def test_version_bump_does_not_duplicate_a_pick(self):
        # strategy_version is in the primary key, so re-syncing the same pick under a
        # new version used to leave the old row behind and count the pick twice.
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            row = {
                "pick_date": "2026-01-02", "ticker": "005930", "name": "Test", "score": 50,
                "factors_json": "{}", "return_1d_pct": 1.0, "return_20d_pct": 2.0,
                "quality_status": "valid", "updated_at": "now",
            }
            upsert_recommendation_outcomes([{**row, "strategy_version": "v5"}], path)
            upsert_recommendation_outcomes([{**row, "strategy_version": "v6"}], path)

            stored = query_rows("SELECT strategy_version FROM recommendation_outcomes", path=path)
            self.assertEqual(["v6"], [item["strategy_version"] for item in stored])

    def test_learning_ignores_picks_without_a_20d_outcome(self):
        # "300건" means 300 picks that have matured to 20 trading days; a row holding
        # only a 1-day return is not a finished observation yet.
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            rows = []
            start = datetime(2026, 1, 1)
            for index in range(360):
                factors = {factor: 1.0 for factor in DEFAULT_WEIGHTS}
                factors["volume_score"] = float(index % 30)
                rows.append({
                    "pick_date": (start + timedelta(days=index)).date().isoformat(), "ticker": f"U{index:03d}",
                    "name": "Test", "strategy_version": "test", "score": 50,
                    "factors_json": json.dumps(factors), "return_1d_pct": float(index % 7),
                    "excess_1d_pct": float(index % 7), "return_20d_pct": None,
                    "quality_status": "valid", "updated_at": "now",
                })
            upsert_recommendation_outcomes(rows, path)

            result = learn(path, datetime(2026, 8, 27, 16, 0))
            self.assertEqual("insufficient_data", result["status"])
            self.assertEqual(0, result["sample_count"])

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
                    "excess_1d_pct": value, "return_20d_pct": value,
                    "quality_status": "valid", "updated_at": "now",
                })
            upsert_recommendation_outcomes(rows, path)
            with patch.dict(os.environ, {"LEARNING_MIN_SAMPLES": "300", "LEARNING_VALIDATION_MIN_SAMPLES": "60", "LEARNING_VALIDATION_FOLDS": "3", "LEARNING_MAX_DAILY_WEIGHT_CHANGE": "0.05"}):
                result = learn(path, datetime(2026, 8, 27, 16, 0))
            self.assertIn(result["status"], {"promoted", "rejected"})
            self.assertLessEqual(abs(result["weights"]["volume_score"] - 1.0), 0.0501)
            self.assertEqual(60.0, adjusted_score({"volume_score": 60}, path))


if __name__ == "__main__":
    unittest.main()
