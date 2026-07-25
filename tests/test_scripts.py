import unittest


class ScriptTest(unittest.TestCase):
    def test_daily_task_runs_sell_check(self):
        with open("scripts/run_stock_alarm.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn('RunStep "recommendation" "stock_alarm"', script)
        self.assertIn('RunStep "sell_check" "stock_alarm.sell_check"', script)
        self.assertIn('RunStep "daily_check" "stock_alarm.daily_check"', script)
        self.assertIn('RunStep "dashboard" "stock_alarm.dashboard"', script)
        self.assertIn("START $name", script)
        self.assertIn("DONE $name", script)
        self.assertIn('$mode = if ($args.Count -gt 0) { $args[0] } else { "daily" }', script)
        self.assertIn('if ($mode -eq "daily" -or $mode -eq "intraday")', script)
        self.assertIn("stock_alarm.failure_alert", script)

    def test_register_task_adds_intraday_checks(self):
        with open("scripts/register_daily_task.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("stockAlarmOpen", script)
        self.assertIn("stockAlarmIntraday1030", script)
        self.assertIn("stockAlarmIntraday1330", script)
        self.assertIn("stockAlarmIntraday1500", script)

    def test_start_macro_registers_and_checks(self):
        with open("start_stock_alarm.bat", encoding="utf-8-sig") as file:
            batch = file.read()
        with open("scripts/start_stock_alarm.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("scripts\\start_stock_alarm.ps1", batch)
        self.assertIn("register_daily_task.ps1", script)
        self.assertIn("stock_alarm.health", script)
        self.assertIn("status_daily_task.ps1", script)
        self.assertIn("stock_alarm.daily_check", script)


if __name__ == "__main__":
    unittest.main()
