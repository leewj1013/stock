import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from stock_alarm import core_satellite_tracker
from stock_alarm.dashboard import experiment_account_rows
from stock_alarm.data_store import virtual_deposit
from stock_alarm.trading_profiles import PROFILES


class ExperimentDashboardTest(unittest.TestCase):
    def test_rows_show_waiting_accounts_and_return_with_drawdown_once_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            control, candidate, core = (os.path.join(directory, f"{name}.db") for name in ("control", "candidate", "core"))
            for path in (control, candidate):
                virtual_deposit(100_000_000, path=path)
            with closing(sqlite3.connect(control)) as connection:
                for cash, equity in ((100_000_000, 100_000_000), (40_000_000, 110_000_000), (50_000_000, 99_000_000)):
                    connection.execute(
                        "INSERT INTO virtual_valuation_snapshots(created_at,cash,holdings_cost,valuation,equity,profit_loss,return_pct,return_change_pct) VALUES('2026-09-16',?,0,0,?,0,0,0)",
                        (cash, equity),
                    )
                connection.commit()
            with patch.dict(PROFILES["exp_control"], {"db_path": control}), \
                 patch.dict(PROFILES["exp_candidate"], {"db_path": candidate}), \
                 patch.object(core_satellite_tracker, "DB_PATH", core):
                rows = experiment_account_rows()

        self.assertEqual(["비교(현재 규칙)", "비교(후보 규칙)", "지수30%+전략70%"], [row["experiment"] for row in rows])
        self.assertEqual(("99,000,000원", "-1.00", "-10.00", "50.5%"), tuple(rows[0][key] for key in ("equity", "total_return_pct", "mdd_pct", "cash_pct")))
        self.assertEqual("기록 대기", rows[1]["risk_state"])
        self.assertEqual("기록 대기", rows[2]["risk_state"])


if __name__ == "__main__":
    unittest.main()
