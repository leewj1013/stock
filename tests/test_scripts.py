import unittest


class ScriptTest(unittest.TestCase):
    def test_daily_task_runs_sell_check(self):
        with open("scripts/run_stock_alarm.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn('RunStep "recommendation" "stock_alarm"', script)
        self.assertIn('RunStep "sell_check" "stock_alarm.sell_check"', script)
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


if __name__ == "__main__":
    unittest.main()
