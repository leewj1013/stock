import unittest
from unittest.mock import patch

from stock_alarm.benchmark_comparison import (
    PortfolioSimulator,
    compound_return,
    daily_return_map,
    percentile_rank,
    select_candidate_rows,
    simple_momentum_volume_ratio,
)


class FakeEngine:
    def __init__(self):
        self.cost_pct = 0.0
        self.regimes = {"2022-01-03": "sideways", "2022-01-04": "bull"}
        self.by_date = {
            "KOSPI": {
                "2022-01-03": (0, ["20220103", 10, 11, 9, 11, 100]),
                "2022-01-04": (1, ["20220104", 11, 12, 10, 12, 100]),
            },
            "A": {
                "2022-01-03": (0, ["20220103", 10, 11, 9, 11, 100]),
                "2022-01-04": (1, ["20220104", 11, 12, 10, 12, 100]),
            },
            "B": {
                "2022-01-03": (0, ["20220103", 20, 21, 19, 20, 100]),
                "2022-01-04": (1, ["20220104", 20, 23, 19, 22, 100]),
            },
        }


class FakeRiskEngine:
    def __init__(self):
        self.cost_pct = 0.0
        self.top_n = 10
        self.minimum_score = 0.0
        self.regimes = {"2022-01-03": "sideways", "2022-01-04": "sideways"}
        tickers = ["KOSPI", *list("ABCDEFGHIJ")]
        self.names = {ticker: ticker for ticker in tickers if ticker != "KOSPI"}
        self.by_date = {
            ticker: {
                "2022-01-03": (0, ["20220103", 10, 10, 10, 10, 100]),
                "2022-01-04": (1, ["20220104", 10, 10, 10, 10, 100]),
            }
            for ticker in tickers
        }

    def _history(self, ticker, day, length=91):
        return [[f"202101{index:02d}", 10, 10, 10, 10, 100] for index in range(1, 22)]

    def _benchmark_trade_return(self, entry_day, exit_day):
        return 0.0

    def _market_up_ratio(self, day):
        return 0.40 if day == "2022-01-04" else 0.70


class BenchmarkComparisonTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"ticker": "A", "score": 70, "volume_ratio": 2.0},
            {"ticker": "B", "score": 80, "volume_ratio": 1.6},
            {"ticker": "C", "score": 40, "volume_ratio": 3.0},
        ]

    def test_stock_alarm_and_momentum_selection_rules(self):
        stock = select_candidate_rows(self.rows, "stock_alarm", 2, minimum_score=50)
        momentum = select_candidate_rows(self.rows, "momentum", 2)
        self.assertEqual(["B", "A"], [row["ticker"] for row in stock])
        self.assertEqual(["C", "A"], [row["ticker"] for row in momentum])

    def test_random_selection_is_repeatable_for_fixed_seed(self):
        first = select_candidate_rows(self.rows, "random", 2, seed=42)
        second = select_candidate_rows(list(reversed(self.rows)), "random", 2, seed=42)
        self.assertEqual([row["ticker"] for row in first], [row["ticker"] for row in second])

    def test_simple_momentum_requires_above_ma20_and_ranks_current_volume(self):
        history = [[f"202201{index:02d}", 10, 11, 9, 10, 100] for index in range(1, 22)]
        history[-1][4] = 12
        history[-1][5] = 250
        self.assertEqual(2.5, simple_momentum_volume_ratio(history))
        history[-1][4] = 9
        self.assertIsNone(simple_momentum_volume_ratio(history))

    def test_kospi_buy_hold_uses_first_open_and_last_close(self):
        result = PortfolioSimulator(FakeEngine(), 100, 10).buy_and_hold("kospi", ["KOSPI"])
        self.assertAlmostEqual(20.0, compound_return(list(daily_return_map(result).values())) * 100)

    def test_equal_weight_buy_hold_does_not_rebalance(self):
        result = PortfolioSimulator(FakeEngine(), 100, 10).buy_and_hold("equal", ["A", "B"])
        # 5 A shares + 2 B shares + 10 cash = 114 on the final day.
        self.assertAlmostEqual(14.0, compound_return(list(daily_return_map(result).values())) * 100)

    def test_percentile_rank(self):
        self.assertEqual(75.0, percentile_rank(3, [1, 2, 3, 4]))

    @patch("stock_alarm.benchmark_comparison.check_position", return_value=None)
    def test_benchmark_engine_blocks_new_signals_when_live_exposure_gate_halts(self, _check):
        engine = FakeRiskEngine()
        first = [
            {"ticker": ticker, "name": ticker, "score": 100, "volume_ratio": 2, "signal_date": "2022-01-03"}
            for ticker in list("ABCDEFGH")
        ]
        second = [
            {"ticker": ticker, "name": ticker, "score": 100, "volume_ratio": 2, "signal_date": "2022-01-04"}
            for ticker in list("IJ")
        ]
        with patch.dict("os.environ", {
            "RISK_MAX_EXPOSURE_PCT": "70", "CORRELATION_LIMIT": "2",
        }):
            result = PortfolioSimulator(engine, 1000, 10, apply_market_exposure_limit=False).run(
                "stock_alarm", {"2022-01-03": first, "2022-01-04": second}, seed=1,
            )
        final_state = result.daily_state[-1]
        self.assertTrue(final_state["defensive_mode"])
        self.assertIn("exposure_limit", final_state["risk_reason"])
        self.assertFalse(any(event.get("ticker") in {"I", "J"} for event in result.events))

    @patch("stock_alarm.benchmark_comparison.check_position", return_value=None)
    def test_execution_day_defensive_breadth_caps_actual_new_buys_at_ten_percent(self, _check):
        engine = FakeRiskEngine()
        candidates = {
            "2022-01-03": [
                {"ticker": ticker, "name": ticker, "score": 100, "volume_ratio": 2, "signal_date": "2022-01-03"}
                for ticker in list("ABC")
            ]
        }
        result = PortfolioSimulator(engine, 1000, 10).run("stock_alarm", candidates, seed=1)
        buys = [event for event in result.events if event["event"] == "buy"]
        self.assertEqual(1, len(buys))
        self.assertLessEqual(sum(event["gross_notional"] for event in buys), 100)
        self.assertEqual(0.40, buys[0]["market_up_ratio"])
        self.assertEqual(10.0, buys[0]["market_exposure_limit_pct"])


if __name__ == "__main__":
    unittest.main()
