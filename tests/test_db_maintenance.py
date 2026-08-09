import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from stock_alarm.data_store import finish_run, start_run, write_candidates
from stock_alarm.db_maintenance import backup_database, integrity_check, prune_old_snapshots


class DbMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "test.db")

    def test_integrity_and_backup(self):
        run_id = start_run("recommendation", "2026-08-04", self.path)
        finish_run(run_id, path=self.path)
        self.assertEqual("ok", integrity_check(self.path))
        backup = backup_database(self.path, str(Path(self.directory.name) / "backups"))
        self.assertTrue(os.path.exists(backup))

    def test_prune_old_rows(self):
        run_id = start_run("recommendation", "2020-01-01", self.path)
        write_candidates(run_id, [{"ticker": "A", "evaluated_at": "2020-01-01T00:00:00", "passed": 0, "selected": 0}], self.path)
        with closing(__import__("sqlite3").connect(self.path)) as connection:
            connection.execute("UPDATE strategy_runs SET started_at='2020-01-01T00:00:00'")
            connection.commit()
        result = prune_old_snapshots(self.path, retention_days=1)
        self.assertEqual(1, result["candidates"])
        self.assertEqual(1, result["runs"])


if __name__ == "__main__":
    unittest.main()
