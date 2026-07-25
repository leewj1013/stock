import unittest


class ScriptTest(unittest.TestCase):
    def test_daily_task_runs_sell_check(self):
        with open("scripts/run_stock_alarm.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn('RunStep "recommendation" "stock_alarm"', script)
        self.assertIn('RunStep "sell_check" "stock_alarm.sell_check"', script)
        self.assertIn("stock_alarm.failure_alert", script)


if __name__ == "__main__":
    unittest.main()
