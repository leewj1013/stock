import os
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from stock_alarm.data_store import connect, virtual_buy, virtual_deposit
from stock_alarm.shadow_trader import (
    _size_buy_orders, compute_shadow_sell_orders, latest_trade_id, plan_shadow_buys, record_intraday_buys,
    record_shadow_orders, run,
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
    def test_plan_sizes_against_the_real_account(self, real_state, _breadth, _regime):
        real_state.return_value = {"connected": True, "cash": 1_000_000, "total_equity": 1_000_000, "holdings": []}
        trades = [{"trade_id": 1, "ticker": "005930", "name": "Samsung", "price": 70000, "allocation_pct": 10.0}]

        orders, _decisions, _context = plan_shadow_buys(trades, path=self.path)

        self.assertEqual(1, len(orders))
        self.assertEqual("005930", orders[0]["ticker"])
        self.assertEqual("BUY", orders[0]["side"])
        self.assertGreater(orders[0]["quantity"], 0)

    @patch("stock_alarm.app.current_market_regime", return_value="bull")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.7)
    @patch("stock_alarm.dashboard.real_account_state")
    def test_plan_rechecks_constraints_against_real_holdings(self, real_state, _breadth, _regime):
        real_state.return_value = {
            "connected": True, "cash": 1_000_000, "total_equity": 2_000_000,
            "holdings": [{"ticker": "005930", "name": "Samsung", "quantity": 10, "average_price": 100000, "current_price": 100000, "valuation": 1_000_000}],
        }
        trades = [{"trade_id": 1, "ticker": "000660", "name": "SK hynix", "price": 150000, "allocation_pct": 10.0}]
        with patch("stock_alarm.app.correlation_limited_allocations", return_value=[50.0, 0.0]) as limited, \
             patch("stock_alarm.app.sector_limited_allocations", side_effect=lambda picks, allocations, **kwargs: allocations):
            orders, decisions, _context = plan_shadow_buys(trades, path=self.path)

        self.assertEqual([], orders)
        self.assertEqual({"005930"}, limited.call_args.kwargs["locked_tickers"])
        self.assertIn("상관관계", decisions[0]["verdict"])

    @patch("stock_alarm.app.current_market_regime", return_value="bull")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.7)
    @patch("stock_alarm.dashboard.real_account_state")
    def test_assumed_capital_sizes_orders_before_any_real_deposit(self, real_state, _breadth, _regime):
        real_state.return_value = {"connected": True, "cash": 450, "total_equity": 450, "holdings": []}
        trades = [{"trade_id": 1, "ticker": "010140", "name": "Samsung Heavy", "price": 22300, "allocation_pct": 10.0}]
        with patch.dict(os.environ, {}):
            os.environ.pop("SHADOW_TRADER_ASSUMED_CAPITAL", None)
            self.assertEqual([], plan_shadow_buys(trades, path=self.path)[0])
        with patch.dict(os.environ, {"SHADOW_TRADER_ASSUMED_CAPITAL": "10000000"}):
            orders = plan_shadow_buys(trades, path=self.path)[0]

        self.assertEqual(1, len(orders))
        self.assertEqual(44, orders[0]["quantity"])
        self.assertIn("가정금액 10,000,000원", orders[0]["reason"])

    @patch("stock_alarm.app.current_market_regime", return_value="bear")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.3)  # 10% market limit
    @patch("stock_alarm.dashboard.real_account_state")
    def test_short_budget_keeps_the_name_bought_first_and_says_why(self, real_state, _breadth, _regime):
        # 2026-09-17: the 16:15 pass read the trades newest-first, so a 1,000만원
        # budget went to the day's LAST buy instead of its first.
        real_state.return_value = {"connected": True, "cash": 0, "total_equity": 0, "holdings": []}
        trades = [
            {"trade_id": 12, "ticker": "LATE", "name": "Late", "price": 10_000, "allocation_pct": 15.0},
            {"trade_id": 11, "ticker": "EARLY", "name": "Early", "price": 10_000, "allocation_pct": 15.0},
        ]
        with patch.dict(os.environ, {"SHADOW_TRADER_ASSUMED_CAPITAL": "100000000"}):
            orders, decisions, context = plan_shadow_buys(trades, path=self.path)

        self.assertEqual(["EARLY"], [order["ticker"] for order in orders])
        self.assertEqual(10.0, context["exposure_limit_pct"])
        self.assertEqual(10_000_000, context["budget"])
        self.assertIn("보유한도 10%", orders[0]["reason"])
        self.assertEqual(["주문", "예산 부족"], [decision["verdict"] for decision in decisions])

    @patch("stock_alarm.app.current_market_regime", return_value="bull")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.5)  # 40% market limit
    @patch("stock_alarm.dashboard.real_account_state")
    def test_earlier_shadow_buys_today_use_up_the_budget(self, real_state, _breadth, _regime):
        real_state.return_value = {"connected": True, "cash": 0, "total_equity": 0, "holdings": []}
        from datetime import date
        record_shadow_orders([{"ticker": "FIRST", "name": "First", "side": "BUY", "order_type": "MARKET",
                               "quantity": 3500, "price": 10_000, "cost": 35_000_000}], path=self.path)
        trades = [{"trade_id": 2, "ticker": "SECOND", "name": "Second", "price": 10_000, "allocation_pct": 15.0}]
        with patch.dict(os.environ, {"SHADOW_TRADER_ASSUMED_CAPITAL": "100000000"}), \
             patch("stock_alarm.shadow_trader.date") as fake_date:
            fake_date.today.return_value = date.today()
            orders, decisions, context = plan_shadow_buys(trades, path=self.path)

        # 40% of 1억 = 4,000만원, 3,500만원 already committed this morning
        self.assertEqual(35_000_000, context["spent_earlier_today"])
        self.assertEqual(5_000_000, context["budget"])
        self.assertEqual([], orders)
        self.assertIn("목표의 50% 미만", decisions[0]["verdict"])

    @patch("stock_alarm.shadow_trader.log_decisions")
    @patch("stock_alarm.shadow_trader.plan_shadow_buys", return_value=([], [], {}))
    def test_intraday_hook_only_shadows_trades_made_after_the_marker(self, plan, _log):
        virtual_deposit(10_000_000, path=self.path)
        virtual_buy([{"ticker": "OLD", "name": "Old", "close": 10_000, "allocation_pct": 10.0}], path=self.path)
        marker = latest_trade_id(self.path)
        virtual_buy([{"ticker": "NEW", "name": "New", "close": 10_000, "allocation_pct": 10.0}], path=self.path)

        result = record_intraday_buys(marker, path=self.path)

        self.assertEqual(1, result["trades"])
        self.assertEqual(["NEW"], [row["ticker"] for row in plan.call_args.args[0]])

    def test_intraday_hook_does_nothing_without_new_trades(self):
        self.assertEqual({"trades": 0, "recorded": 0}, record_intraday_buys(0, path=self.path))

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

    @patch("stock_alarm.shadow_trader.compute_shadow_sell_orders")
    @patch("stock_alarm.app.load_env")
    def test_after_close_run_records_only_sells(self, _load_env, sell_orders):
        # Buys are recorded intraday now; a second pass here would double them.
        sell_orders.return_value = [
            {"ticker": "005930", "name": "Samsung", "side": "SELL", "order_type": "MARKET", "quantity": 10, "price": 70000, "cost": 0, "reason": "종목 경고"},
        ]

        result = run(path=self.path)

        self.assertEqual({"buy_orders": 0, "sell_orders": 1, "recorded": 1}, result)


if __name__ == "__main__":
    unittest.main()
