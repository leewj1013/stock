import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.data_store import finish_run, import_legacy_virtual_trader, record_virtual_valuation, start_run, virtual_buy, virtual_deposit, virtual_sell, virtual_trader_state, write_candidates, write_position_checks, write_sell_outcomes


class DataStoreTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.path = handle.name
        handle.close()
        os.unlink(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

    def test_records_candidate_run_and_snapshot(self):
        run_id = start_run("recommendation", "2026-08-04", self.path)
        write_candidates(run_id, [{"ticker": "005930", "name": "Samsung", "evaluated_at": "now", "legacy_score": 75, "legacy_passed": 1, "passed": 1, "selected": 1, "final_score": 80, "rejection_reasons": ""}], self.path)
        finish_run(run_id, path=self.path)

        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(("completed",), connection.execute("SELECT status FROM strategy_runs").fetchone())
            self.assertEqual(("005930", 1), connection.execute("SELECT ticker, selected FROM candidate_snapshots").fetchone())
        finally:
            connection.close()

    def test_sell_outcomes_are_replaced_as_a_snapshot(self):
        common = {"alert_created_at": "2026-08-01", "ticker": "A", "updated_at": "now"}
        write_sell_outcomes([{**common, "alert_key": "old"}], self.path)
        write_sell_outcomes([{**common, "alert_key": "new"}], self.path)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual([("new",)], connection.execute("SELECT alert_key FROM sell_outcomes").fetchall())
        finally:
            connection.close()

    def test_records_hold_position_check(self):
        run_id = start_run("sell_check", "2026-08-04", self.path)
        write_position_checks(run_id, [{"position_id": "p1", "checked_at": "now", "ticker": "005930", "stop_loss_triggered": 0, "ma20_break_triggered": 0, "return_drop_triggered": 0, "giveback_triggered": 0, "decision": "HOLD", "reasons": ""}], self.path)

        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(("HOLD",), connection.execute("SELECT decision FROM position_checks").fetchone())
        finally:
            connection.close()

    def test_virtual_trader_deposit_and_integer_weighted_buy_are_persisted(self):
        virtual_deposit(100_000, self.path)
        result = virtual_buy([
            {"ticker": "A", "name": "Alpha", "close": 10_000, "score": 80, "allocation_pct": 60},
            {"ticker": "B", "name": "Beta", "close": 7_000, "score": 70, "allocation_pct": 40},
        ], self.path)

        self.assertLessEqual(result["spent"], 100_000)
        self.assertEqual(2, result["bought"])
        state = virtual_trader_state({"A": 11_000, "B": 7_000}, self.path)
        self.assertEqual(2, len(state["holdings"]))
        self.assertTrue(all(isinstance(row["quantity"], int) for row in state["holdings"]))
        self.assertEqual(state["cash"] + state["holdings_value"], state["total_equity"])
        self.assertEqual(100_000, state["deposited"])
        self.assertEqual(5.17, state["holdings_return_pct"])
        self.assertEqual(3.0, state["total_return_pct"])

    def test_virtual_buy_uses_total_account_target_and_preserves_cash(self):
        virtual_deposit(100_000, self.path)
        result = virtual_buy([
            {"ticker": "A", "name": "Alpha", "close": 10_000, "score": 80, "allocation_pct": 20},
        ], self.path)

        self.assertEqual(20_000, result["spent"])
        self.assertEqual(80_000, result["cash"])
        with self.assertRaises(ValueError):
            virtual_buy([
                {"ticker": "A", "name": "Alpha", "close": 10_000, "score": 80, "allocation_pct": 20},
            ], self.path)

    def test_virtual_valuation_records_change_from_previous_batch(self):
        virtual_deposit(100_000, self.path)
        virtual_buy([{"ticker": "A", "name": "Alpha", "close": 10_000, "score": 80, "allocation_pct": 20}], self.path)

        first = record_virtual_valuation({"A": 11_000}, self.path)
        second = record_virtual_valuation({"A": 12_000}, self.path)

        self.assertEqual(10.0, first["return_pct"])
        self.assertEqual(20.0, second["return_pct"])
        self.assertEqual(10.0, second["return_change_pct"])

    def test_imports_legacy_browser_state_only_once(self):
        legacy = {"cash": 5000, "holdings": {"A": {"ticker": "A", "name": "Alpha", "quantity": 2, "cost": 2000, "buyPrice": 1000}}}
        self.assertTrue(import_legacy_virtual_trader(legacy, self.path))
        self.assertFalse(import_legacy_virtual_trader(legacy, self.path))
        self.assertEqual(5000, virtual_trader_state(path=self.path)["cash"])

    def test_virtual_sell_closes_position_and_returns_proceeds_to_cash(self):
        virtual_deposit(100_000, self.path)
        virtual_buy([{"ticker": "A", "name": "Alpha", "close": 10_000, "score": 80, "allocation_pct": 100}], self.path)

        result = virtual_sell([{"ticker": "A", "name": "Alpha", "close": 11_000, "reason": "take profit"}], self.path)

        self.assertEqual(1, result["sold"])
        self.assertEqual([], result["holdings"])
        self.assertEqual(103_000, result["cash"])

    def test_partial_take_profit_records_state_then_second_stage_closes_remainder(self):
        virtual_deposit(100_000, self.path)
        with patch.dict(os.environ, {"VIRTUAL_TRADER_MAX_POSITION_PCT": "100", "RISK_MAX_EXPOSURE_PCT": "100"}):
            virtual_buy([{"ticker": "A", "name": "Alpha", "close": 10_000, "score": 80, "allocation_pct": 100}], self.path)

        first = virtual_sell([{"ticker": "A", "name": "Alpha", "close": 11_000, "reason": "1차 익절", "sale_type": "partial", "stage": "take_profit_1", "quantity_fraction": 0.5}], self.path)
        self.assertEqual(5, first["executions"][0]["quantity"])
        self.assertEqual("partial", first["holdings"][0]["position_status"])
        self.assertEqual(5, first["holdings"][0]["quantity"])

        second = virtual_sell([{"ticker": "A", "name": "Alpha", "close": 12_000, "reason": "2차 익절", "sale_type": "full", "stage": "take_profit_2"}], self.path)
        self.assertEqual([], second["holdings"])
        self.assertEqual("take_profit_2", second["executions"][0]["stage"])

    def test_portfolio_halt_does_not_block_individual_virtual_sell(self):
        from datetime import datetime
        from stock_alarm.portfolio_risk import snapshot

        virtual_deposit(100_000, self.path)
        virtual_buy([{"ticker": "A", "name": "Alpha", "close": 10_000, "score": 80, "allocation_pct": 20}], self.path)
        snapshot({"total_equity": 100_000, "holdings_value": 80_000}, self.path, datetime(2026, 8, 27, 9, 0))
        result = virtual_sell([{"ticker": "A", "name": "Alpha", "close": 9_000, "reason": "개별 손절"}], self.path)
        self.assertEqual(1, result["sold"])
        self.assertEqual([], result["holdings"])


if __name__ == "__main__":
    unittest.main()
