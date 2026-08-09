import os
import sqlite3
import tempfile
import unittest

from stock_alarm.data_store import finish_run, start_run, write_candidates, write_position_checks


class DataStoreTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.path = handle.name
        handle.close()
        os.unlink(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

    def test_records_candidate_run_and_snapshot(self):
        run_id = start_run("recommendation", "2026-08-04", self.path)
        write_candidates(run_id, [{"ticker": "005930", "name": "Samsung", "evaluated_at": "now", "passed": 1, "selected": 1, "final_score": 80, "rejection_reasons": ""}], self.path)
        finish_run(run_id, path=self.path)

        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(("completed",), connection.execute("SELECT status FROM strategy_runs").fetchone())
            self.assertEqual(("005930", 1), connection.execute("SELECT ticker, selected FROM candidate_snapshots").fetchone())
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


if __name__ == "__main__":
    unittest.main()
