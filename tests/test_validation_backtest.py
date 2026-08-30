import unittest
from datetime import date, timedelta
from unittest.mock import patch

from stock_alarm.app import evaluate_naver_candidate
from stock_alarm.backtest_data import label_market_regimes, validate_backtest_rows
from stock_alarm.strategy_learning import DEFAULT_WEIGHTS


def price_rows(closes):
    start = date(2026, 1, 1)
    return [
        [(start + timedelta(days=index)).strftime("%Y%m%d"), close, close + 2, close - 2, close, 100]
        for index, close in enumerate(closes)
    ]


class ValidationBacktestTest(unittest.TestCase):
    def test_regime_labeling_classifies_bull_bear_and_sideways(self):
        bull = label_market_regimes(price_rows([100, 100, 100, 110]), ma_days=3, return_days=2, trend_threshold_pct=5)
        bear = label_market_regimes(price_rows([100, 100, 100, 90]), ma_days=3, return_days=2, trend_threshold_pct=5)
        sideways = label_market_regimes(price_rows([100, 100, 100, 101]), ma_days=3, return_days=2, trend_threshold_pct=5)
        self.assertEqual("bull", bull[-1]["regime"])
        self.assertEqual("bear", bear[-1]["regime"])
        self.assertEqual("sideways", sideways[-1]["regime"])

    def test_historical_quality_uses_live_ohlcv_rules(self):
        rows = price_rows([100, 101])
        rows.append(["20260103", 100, 90, 110, 100, -1])
        valid, issues = validate_backtest_rows("A", rows)
        self.assertEqual(2, len(valid))
        self.assertEqual("invalid_ohlc", issues[0]["reason"])

    def test_backtest_price_array_matches_live_evaluator_for_same_input(self):
        rows = price_rows([100] * 20 + [102])
        rows[-1][5] = 200
        direct = evaluate_naver_candidate(
            "A", "Alpha", date(2026, 1, 21), 0, 1.5,
            price_rows=rows, external_lookup=False, score_weights=DEFAULT_WEIGHTS,
        )
        with patch("stock_alarm.app.naver_rows", return_value=rows):
            live_path = evaluate_naver_candidate(
                "A", "Alpha", date(2026, 1, 21), 0, 1.5,
                external_lookup=False, score_weights=DEFAULT_WEIGHTS,
            )
        self.assertIsNotNone(direct.pick)
        self.assertEqual(direct.pick, live_path.pick)
        self.assertEqual(direct.values["final_score"], live_path.values["final_score"])


if __name__ == "__main__":
    unittest.main()
