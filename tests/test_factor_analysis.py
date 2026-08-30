import unittest
from datetime import date, timedelta

from stock_alarm.app import evaluate_naver_candidate
from stock_alarm.factor_analysis import (
    cross_sectional_quantiles,
    daily_information_coefficient,
    extract_evaluation_factors,
    is_monotonic,
    spearman_correlation,
)
from stock_alarm.strategy_learning import DEFAULT_WEIGHTS, FACTORS
from stock_alarm.validation_backtest import BacktestEngine


def price_rows(closes, volumes=None):
    start = date(2026, 1, 1)
    volumes = volumes or [100] * len(closes)
    return [
        [(start + timedelta(days=index)).strftime("%Y%m%d"), close, close + 2, close - 2, close, volumes[index]]
        for index, close in enumerate(closes)
    ]


class FactorAnalysisTest(unittest.TestCase):
    def test_factor_values_are_exact_live_evaluator_values(self):
        rows = price_rows([100] * 20 + [102], [100] * 20 + [200])
        evaluation = evaluate_naver_candidate(
            "A", "Alpha", date(2026, 1, 21), 0, 1.5,
            price_rows=rows, external_lookup=False, score_weights=DEFAULT_WEIGHTS,
        )
        self.assertIsNotNone(evaluation.pick)
        extracted = extract_evaluation_factors(evaluation)
        self.assertEqual(set(FACTORS), set(extracted))
        for factor in FACTORS:
            self.assertEqual(float(evaluation.values.get(factor) or 0), extracted[factor])

    def test_spearman_detects_order_and_constant_factor(self):
        rho, p_value, sample_count = spearman_correlation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
        self.assertEqual(5, sample_count)
        self.assertAlmostEqual(1.0, rho)
        self.assertEqual(0.0, p_value)
        constant_rho, constant_p, _ = spearman_correlation([1, 1, 1], [1, 2, 3])
        self.assertIsNone(constant_rho)
        self.assertIsNone(constant_p)

    def test_daily_ic_and_quantiles_use_cross_sections(self):
        records = []
        for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
            for value in range(1, 11):
                records.append({"signal_date": day, "volume_score": value, "excess_5d_pct": value * 0.5})
        metric = daily_information_coefficient(records, "volume_score", "excess_5d_pct", 5)
        self.assertEqual(3, metric["period_count"])
        self.assertAlmostEqual(1.0, metric["mean_daily_ic"])
        self.assertEqual(0.0, metric["ic_p_value"])
        quantiles = cross_sectional_quantiles(records, "volume_score", "excess_5d_pct", 5)
        self.assertTrue(is_monotonic(quantiles))
        self.assertLess(quantiles[0]["mean_return_pct"], quantiles[-1]["mean_return_pct"])

    def test_history_and_forward_returns_do_not_use_signal_day_future_data(self):
        engine = BacktestEngine.__new__(BacktestEngine)
        stock_rows = price_rows([100, 101, 102, 110, 120])
        benchmark_rows = price_rows([200, 201, 202, 204, 206])
        engine.rows = {"A": stock_rows, "KOSPI": benchmark_rows}
        engine.by_date = {
            ticker: {
                date.fromisoformat(f"2026-01-{index + 1:02d}").isoformat(): (index, row)
                for index, row in enumerate(rows)
            }
            for ticker, rows in engine.rows.items()
        }
        engine.cost_pct = 0.4
        history = engine._history("A", "2026-01-03")
        self.assertEqual(3, len(history))
        self.assertEqual("20260103", history[-1][0])
        outcomes = engine.forward_outcomes("A", "2026-01-03", (1,))
        self.assertEqual("2026-01-04", outcomes["entry_date"])
        self.assertAlmostEqual((120 / 110 - 1) * 100 - 0.4, outcomes["return_1d_pct"])


if __name__ == "__main__":
    unittest.main()
