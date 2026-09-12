import os
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from stock_alarm.data_store import connect, virtual_buy, virtual_deposit
from stock_alarm.shadow_trader import (
    _size_buy_orders, compute_shadow_buy_orders, compute_shadow_sell_orders, record_shadow_orders, run,
)


class ShadowTraderTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.path = handle.name
        handle.close()
        os.unlink(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

    @patch.dict(os.environ, {"VIRTUAL_TRADER_MAX_POSITION_PCT": "100", "RISK_MAX_EXPOSURE_PCT": "100"})
    def test_size_buy_orders_matches_virtual_buy_for_the_same_inputs(self):
        virtual_deposit(100_000, self.path)
        candidates = [
            {"ticker": "A", "name": "Alpha", "close": 9_000, "score": 80, "allocation_pct": 90},
            {"ticker": "B", "name": "Beta", "close": 5_000, "score": 70, "allocation_pct": 30},
        ]
        result = virtual_buy(candidates, self.path)

        shadow_orders = _size_buy_orders(
            [{"ticker": row["ticker"], "name": row["name"], "close": row["close"], "allocation_pct": row["allocation_pct"]} for row in candidates],
            cash=100_000, open_costs={}, account_equity=100_000,
            max_position_pct=100, min_fill_ratio=0.5, portfolio_budget=100_000,
        )

        self.assertEqual(
            sorted((execution["ticker"], execution["quantity"], execution["cost"]) for execution in result["executions"]),
            sorted((order["ticker"], order["quantity"], order["cost"]) for order in shadow_orders),
        )

    def test_size_buy_orders_skips_below_min_fill_ratio(self):
        orders = _size_buy_orders(
            [{"ticker": "A", "close": 9_000, "allocation_pct": 90}],
            cash=100_000, open_costs={}, account_equity=100_000,
            max_position_pct=100, min_fill_ratio=0.9, portfolio_budget=10_000,
        )
        self.assertEqual([], orders)

    @patch("stock_alarm.app.current_market_regime", return_value="bull")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.7)
    @patch("stock_alarm.dashboard.real_account_state")
    @patch("stock_alarm.data_store.recent_virtual_trades")
    def test_compute_shadow_buy_orders_sizes_against_the_real_account(self, recent_trades, real_state, _breadth, _regime):
        recent_trades.return_value = [
            {"created_at": "2026-09-12T16:00:00", "ticker": "005930", "name": "Samsung", "price": 70000, "quantity": 1, "cost": 70000, "allocation_pct": 10.0},
        ]
        real_state.return_value = {
            "connected": True, "cash": 1_000_000, "total_equity": 1_000_000, "holdings": [],
        }
        with patch("stock_alarm.shadow_trader.date") as fake_date:
            fake_date.today.return_value = __import__("datetime").date(2026, 9, 12)
            orders = compute_shadow_buy_orders(path=self.path)

        self.assertEqual(1, len(orders))
        self.assertEqual("005930", orders[0]["ticker"])
        self.assertEqual("BUY", orders[0]["side"])
        self.assertGreater(orders[0]["quantity"], 0)

    @patch("stock_alarm.data_store.recent_virtual_trades", return_value=[])
    def test_compute_shadow_buy_orders_returns_nothing_without_todays_virtual_trades(self, _trades):
        self.assertEqual([], compute_shadow_buy_orders(path=self.path))

    @patch("stock_alarm.dashboard.real_account_state")
    def test_compute_shadow_sell_orders_flags_only_blocked_holdings(self, real_state):
        real_state.return_value = {
            "connected": True,
            "holdings": [
                {"ticker": "005930", "name": "Samsung", "quantity": 10, "current_price": 70000, "watch_state": "종목 경고: LIQUIDATION_TRADING"},
                {"ticker": "000660", "name": "SK hynix", "quantity": 5, "current_price": 150000, "watch_state": "정상 보유"},
            ],
        }

        orders = compute_shadow_sell_orders()

        self.assertEqual(1, len(orders))
        self.assertEqual("005930", orders[0]["ticker"])
        self.assertEqual("SELL", orders[0]["side"])
        self.assertEqual(10, orders[0]["quantity"])

    @patch("stock_alarm.dashboard.real_account_state", return_value={"connected": False})
    def test_compute_shadow_sell_orders_returns_nothing_when_not_connected(self, _state):
        self.assertEqual([], compute_shadow_sell_orders())

    def test_record_shadow_orders_persists_rows(self):
        recorded = record_shadow_orders([
            {"ticker": "005930", "name": "Samsung", "side": "BUY", "order_type": "MARKET", "quantity": 10, "price": 70000, "cost": 700000, "reason": "recommendation"},
        ], path=self.path)

        self.assertEqual(1, recorded)
        with closing(connect(self.path)) as connection:
            row = connection.execute("SELECT ticker, side, quantity FROM shadow_orders").fetchone()
        self.assertEqual(("005930", "BUY", 10), tuple(row))

    def test_record_shadow_orders_is_a_noop_for_an_empty_list(self):
        self.assertEqual(0, record_shadow_orders([], path=self.path))

    @patch("stock_alarm.shadow_trader.compute_shadow_sell_orders", return_value=[])
    @patch("stock_alarm.shadow_trader.compute_shadow_buy_orders")
    @patch("stock_alarm.app.load_env")
    def test_run_records_computed_orders(self, _load_env, buy_orders, _sell_orders):
        buy_orders.return_value = [
            {"ticker": "005930", "name": "Samsung", "side": "BUY", "order_type": "MARKET", "quantity": 10, "price": 70000, "cost": 700000, "reason": "recommendation"},
        ]

        result = run(path=self.path)

        self.assertEqual({"buy_orders": 1, "sell_orders": 0, "recorded": 1}, result)


if __name__ == "__main__":
    unittest.main()
