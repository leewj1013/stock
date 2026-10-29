import os
import tempfile
import unittest
from contextlib import closing, ExitStack
from pathlib import Path
from unittest.mock import patch

from stock_alarm.data_store import finish_run, start_run, write_candidates
from stock_alarm.db_maintenance import backup_database, integrity_check, prune_old_snapshots, run_all_profiles


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

    def test_backup_prefixes_by_db_filename_so_profiles_dont_share_retention(self):
        backup_dir = str(Path(self.directory.name) / "backups")
        aggressive_path = str(Path(self.directory.name) / "stock_alarm.db")
        neutral_path = str(Path(self.directory.name) / "stock_alarm_neutral.db")
        start_run("recommendation", "2026-08-04", aggressive_path)
        start_run("recommendation", "2026-08-04", neutral_path)

        aggressive_backup = backup_database(aggressive_path, backup_dir)
        neutral_backup = backup_database(neutral_path, backup_dir)

        self.assertTrue(Path(aggressive_backup).name.startswith("stock_alarm-"))
        self.assertTrue(Path(neutral_backup).name.startswith("stock_alarm_neutral-"))

    def test_run_all_profiles_backs_up_every_profile_db_that_exists(self):
        aggressive_path = str(Path(self.directory.name) / "stock_alarm.db")
        neutral_path = str(Path(self.directory.name) / "stock_alarm_neutral.db")
        start_run("recommendation", "2026-08-04", aggressive_path)
        start_run("recommendation", "2026-08-04", neutral_path)
        profiles = {
            "aggressive": {"db_path": aggressive_path},
            "neutral": {"db_path": neutral_path},
            "missing": {"db_path": str(Path(self.directory.name) / "does_not_exist.db")},
        }

        from stock_alarm import db_maintenance

        real_backup = db_maintenance.backup_database
        backups = str(Path(self.directory.name) / "backups")
        # The default backup_dir is the real data/backups, and backup_database
        # prunes by file-name prefix -- a test run there used to push out the
        # real stock_alarm / stock_alarm_neutral backups.
        with patch("stock_alarm.trading_profiles.PROFILES", profiles), \
             patch.object(db_maintenance, "backup_database", lambda path, **kw: real_backup(path, backup_dir=backups, **kw)):
            results = run_all_profiles()

        self.assertEqual({"aggressive", "neutral"}, set(results.keys()))
        self.assertEqual("ok", results["aggressive"]["integrity"])
        self.assertEqual("ok", results["neutral"]["integrity"])

    def test_prune_old_rows(self):
        run_id = start_run("recommendation", "2020-01-01", self.path)
        write_candidates(run_id, [{"ticker": "A", "evaluated_at": "2020-01-01T00:00:00", "passed": 0, "selected": 0}], self.path)
        with closing(__import__("sqlite3").connect(self.path)) as connection:
            connection.execute("UPDATE strategy_runs SET started_at='2020-01-01T00:00:00'")
            connection.commit()
        result = prune_old_snapshots(self.path, retention_days=1)
        self.assertEqual(1, result["candidates"])
        self.assertEqual(1, result["runs"])

    def test_main_reports_each_research_failure_by_name(self):
        from stock_alarm import db_maintenance
        jobs={'sell':'stock_alarm.sell_research.run','entry':'stock_alarm.entry_research.run',
              'portfolio':'stock_alarm.portfolio_research.run','earnings':'stock_alarm.earnings_research.run',
              'theme':'stock_alarm.theme_research.run','risk':'stock_alarm.risk_research.run',
              'profit':'stock_alarm.profit_research.run','dips':'stock_alarm.profit_research.run_dips',
              'extensions':'stock_alarm.research_extensions.run','evidence':'stock_alarm.research_data_collect.run_daily',
              'statement_history':'stock_alarm.research_data_collect.backfill_statements','krx':'stock_alarm.krx_research_collect.run',
              'horizon':'stock_alarm.domestic_horizon_research.run','market_groups':'stock_alarm.market_group_snapshot.collect',
              'us':'stock_alarm.us_research.run'}
        for failed in (None,'profit','dips','extensions','risk'):
            with self.subTest(failed=failed),ExitStack() as stack:
                for name,target in jobs.items():
                    stack.enter_context(patch(target,return_value={'failures':int(name==failed)}))
                stack.enter_context(patch('stock_alarm.app.load_env'))
                stack.enter_context(patch.object(db_maintenance,'run_all_profiles',return_value={}))
                stack.enter_context(patch.object(db_maintenance,'backup_database'))
                stack.enter_context(patch('stock_alarm.research_input_gate.run',return_value={}))
                stack.enter_context(patch('stock_alarm.research_followup.run',return_value={'quote_exit_counts':{}}))
                stack.enter_context(patch('builtins.print'))
                if failed:
                    with self.assertRaisesRegex(RuntimeError,failed):db_maintenance.main()
                else:db_maintenance.main()


if __name__ == "__main__":
    unittest.main()
