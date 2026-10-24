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
    def test_stock_cash_accounts_show_actual_cash_next_to_target(self):
        with tempfile.TemporaryDirectory() as directory:
            neutral, aggressive = (os.path.join(directory, f"{name}.db") for name in ("neutral", "aggressive"))
            for path in (neutral, aggressive):
                virtual_deposit(100_000_000, path=path)
            with closing(sqlite3.connect(neutral)) as connection:
                connection.execute("INSERT INTO virtual_valuation_snapshots(created_at,cash,holdings_cost,valuation,equity,profit_loss,return_pct,return_change_pct) VALUES('2026-10-23',50000000,50000000,50000000,100000000,0,0,0)")
                connection.commit()
            with patch.dict(PROFILES["exp_cash_neutral"], {"db_path": neutral}), patch.dict(PROFILES["exp_stock_aggressive"], {"db_path": aggressive}):
                rows = experiment_account_rows()
        by_name = {row["experiment"]: row for row in rows}
        self.assertIn("40~60%", by_name["현금완충 주식형"]["rule"])
        self.assertEqual("50.0%", by_name["현금완충 주식형"]["cash_pct"])
        self.assertIn("80~100%", by_name["주식중심 성장형"]["rule"])
        self.assertEqual("기록 대기", by_name["주식중심 성장형"]["risk_state"])

    def test_rows_show_waiting_accounts_and_return_with_drawdown_once_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            control, candidate, core, index70 = (os.path.join(directory, f"{name}.db") for name in ("control", "candidate", "core", "index70"))
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
                 patch.dict(PROFILES["exp_cash_neutral"], {"db_path": os.path.join(directory, "unfunded_neutral.db")}), \
                 patch.dict(PROFILES["exp_stock_aggressive"], {"db_path": os.path.join(directory, "unfunded_aggressive.db")}), \
                 patch.object(core_satellite_tracker, "DB_PATH", core),                  patch.object(core_satellite_tracker, "CASH_LAYOUTS", (("index70", index70, 0.7, "지수70%+현금30%"), ("index90", index70 + "90", 0.9, "지수90%+현금10%"))):
                rows = experiment_account_rows()

        self.assertEqual(["비교(현재 규칙)", "비교(후보 규칙)", "지수30%+전략70%", "지수70%+현금30%", "지수90%+현금10%"], [row["experiment"] for row in rows])
        self.assertEqual(("99,000,000원", "-1.00", "-10.00", "50.5%"), tuple(rows[0][key] for key in ("equity", "total_return_pct", "mdd_pct", "cash_pct")))
        self.assertEqual("기록 대기", rows[1]["risk_state"])
        self.assertEqual("기록 대기", rows[2]["risk_state"])
        self.assertEqual("기록 대기", rows[3]["risk_state"])
        self.assertEqual("기록 대기", rows[4]["risk_state"])


if __name__ == "__main__":
    unittest.main()
