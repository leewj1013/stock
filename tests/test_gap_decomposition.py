import unittest

from stock_alarm.gap_decomposition import (
    aggregate_transaction_costs,
    post_sale_tracking,
    weighted_cash_missed_returns,
)


class FakeEngine:
    def __init__(self):
        self.rows = {
            "A": [
                ["20220103", 100, 100, 100, 100, 10],
                ["20220104", 105, 105, 105, 105, 10],
                ["20220105", 120, 120, 120, 120, 10],
            ],
            "KOSPI": [
                ["20220103", 100, 100, 100, 100, 10],
                ["20220104", 101, 101, 101, 101, 10],
                ["20220105", 110, 110, 110, 110, 10],
            ],
        }
        self.by_date = {
            ticker: {"2022-01-03": (0, rows[0]), "2022-01-04": (1, rows[1]), "2022-01-05": (2, rows[2])}
            for ticker, rows in self.rows.items()
        }


class GapDecompositionTest(unittest.TestCase):
    def test_cash_missed_return_uses_previous_close_cash_weight(self):
        states = [
            {"date": "2022-01-03", "cash_weight": 0.5, "defensive_mode": True},
            {"date": "2022-01-04", "cash_weight": 0.2, "defensive_mode": False},
        ]
        rows = weighted_cash_missed_returns(states, {"2022-01-03": 0.10, "2022-01-04": 0.20})
        self.assertAlmostEqual(0.10, rows[0]["missed_return"])
        self.assertAlmostEqual(0.10, rows[1]["missed_return"])
        self.assertFalse(rows[0]["defensive_mode"])
        self.assertTrue(rows[1]["defensive_mode"])

    def test_post_sale_tracking_uses_exact_trading_day_horizon(self):
        events = [{
            "date": "2022-01-03", "event": "sell_full", "ticker": "A", "price": 100,
            "gross_notional": 100, "portfolio_equity_before": 1000, "reason": "stop_loss", "stage": "sell",
        }]
        result = post_sale_tracking(events, FakeEngine(), horizons=(2,))
        self.assertAlmostEqual(20.0, result[0]["post_2d_return_pct"])
        self.assertTrue(result[0]["post_2d_rebounded"])
        self.assertAlmostEqual(2.0, result[0]["opportunity_contribution_2d_pp"])

    def test_transaction_cost_aggregation_and_turnover(self):
        events = [
            {"event": "buy", "gross_notional": 1000, "transaction_cost": 0},
            {"event": "sell_full", "gross_notional": 1100, "transaction_cost": 4},
        ]
        result = aggregate_transaction_costs(events, initial_cash=1000, years=2, average_equity=1000)
        self.assertEqual(1, result["sell_event_count"])
        self.assertAlmostEqual(0.4, result["cost_pct_initial_capital"])
        self.assertAlmostEqual(1.05, result["annual_turnover"])


if __name__ == "__main__":
    unittest.main()
