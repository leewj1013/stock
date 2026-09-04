import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.profile_weight_validation import _bucket_by_fold, _fold_boundaries, validate_profile
from stock_alarm.validation_backtest import Trade


def _trade(exit_date: str, return_pct: float) -> Trade:
    return Trade("005930", "Samsung", "2024-01-01", "2024-01-02", exit_date, 100, 100, return_pct, 0.0, return_pct, "sideways", "test", False)


class ProfileWeightValidationTest(unittest.TestCase):
    def test_fold_boundaries_span_the_full_trade_history_evenly(self):
        trades = [_trade("2024-01-01", 1.0), _trade("2024-01-11", 1.0)]
        boundaries = _fold_boundaries(trades, fold_count=2)
        self.assertEqual(date(2024, 1, 1), boundaries[0])
        self.assertEqual(date(2024, 1, 12), boundaries[-1])
        self.assertEqual(3, len(boundaries))

    def test_bucket_by_fold_groups_trades_into_the_matching_calendar_window(self):
        trades = [_trade("2024-01-01", 5.0), _trade("2024-01-10", -2.0)]
        boundaries = [date(2024, 1, 1), date(2024, 1, 6), date(2024, 1, 12)]
        folds = _bucket_by_fold(trades, boundaries)
        self.assertEqual([5.0], [trade.return_pct for trade in folds[0]])
        self.assertEqual([-2.0], [trade.return_pct for trade in folds[1]])

    @patch("stock_alarm.profile_weight_validation.BacktestEngine")
    def test_validate_profile_rejects_when_a_fold_has_no_matching_trades(self, engine_cls):
        baseline_trades = [_trade("2024-01-01", 1.0), _trade("2024-06-01", 1.0)]
        profile_trades = [_trade("2024-01-01", 2.0)]  # nothing in the second half
        engine_cls.return_value.run.return_value = (profile_trades, [])

        result = validate_profile("neutral", fold_count=2, baseline_trades=baseline_trades)

        self.assertEqual("inconsistent_or_not_significant", result["status"])
        self.assertEqual("insufficient_samples_in_fold", result["folds"][1]["decision_reason"])

    @patch("stock_alarm.profile_weight_validation.BacktestEngine")
    def test_validate_profile_reports_relaxed_status_and_effect_size(self, engine_cls):
        baseline_trades = [_trade("2024-01-01", 1.0), _trade("2024-01-02", 1.0)]
        profile_trades = [_trade("2024-01-01", 5.0), _trade("2024-01-02", 5.0)]
        engine_cls.return_value.run.return_value = (profile_trades, [])

        result = validate_profile("neutral", fold_count=1, baseline_trades=baseline_trades)

        self.assertEqual(1.0, result["overall_effect_size"])
        self.assertEqual(1.0, result["folds"][0]["effect_size"])
        self.assertIn(result["relaxed_status"], {"directionally_better_and_significant_overall", "no_significant_overall_improvement"})


if __name__ == "__main__":
    unittest.main()
