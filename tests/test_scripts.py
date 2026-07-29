import unittest


class ScriptTest(unittest.TestCase):
    def test_daily_task_runs_sell_check(self):
        with open("scripts/run_stock_alarm.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn('RunFreshStep "recommendation" "stock_alarm"', script)
        self.assertIn('RunFreshStep "market_summary" "stock_alarm.market_summary"', script)
        self.assertIn('RunFreshStep "sell_check" "stock_alarm.sell_check"', script)
        self.assertIn('RunFreshStep "positions_report" "stock_alarm.positions_report"', script)
        self.assertIn('RunFreshStep "recommendation_performance" "stock_alarm.recommendation_performance"', script)
        self.assertIn('RunStep "daily_check" "stock_alarm.daily_check"', script)
        self.assertIn('RunStep "dashboard" "stock_alarm.dashboard"', script)
        self.assertIn('RunStep "issue_alert" "stock_alarm.issue_alert"', script)
        self.assertIn("START $name", script)
        self.assertIn("DONE $name", script)
        self.assertIn('$mode = if ($args.Count -gt 0) { $args[0] } else { "daily" }', script)
        self.assertIn('if ($mode -eq "daily" -or $mode -eq "intraday")', script)
        self.assertIn('if ($mode -eq "daily" -or $mode -eq "open" -or $mode -eq "intraday")', script)
        self.assertIn('if ($mode -eq "daily" -or $mode -eq "issue_alert")', script)
        self.assertIn("stock_alarm.failure_alert", script)
        self.assertIn("cmd.exe /d /c", script)
        self.assertIn("Set-Content -Path $stdout -Encoding utf8", script)
        self.assertIn('$env:NO_CACHE = "1"', script)

    def test_register_task_adds_intraday_checks(self):
        with open("scripts/register_daily_task.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("stockAlarmOpen", script)
        self.assertIn("stockAlarmIntradayEveryMinute", script)
        self.assertIn("schtasks.exe /Create", script)
        self.assertIn("/SC DAILY /ST 09:00 /RI 1 /DU 006:30", script)
        self.assertIn("Unregister-ScheduledTask -TaskName \"stockAlarmIntraday1030\"", script)
        self.assertIn("Unregister-ScheduledTask -TaskName \"stockAlarmIntraday1330\"", script)
        self.assertIn("Unregister-ScheduledTask -TaskName \"stockAlarmIntraday1500\"", script)

    def test_status_task_shows_next_run(self):
        with open("scripts/status_daily_task.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("NextRunTime", script)
        self.assertIn("next_run=", script)
        self.assertIn("yyyy-MM-dd HH:mm:ss", script)
        self.assertIn("stockAlarmIntradayEveryMinute", script)

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
        self.assertIn("stock_alarm.dashboard", script)
        self.assertIn("Start-Process", script)
        self.assertIn("open_check.bat", script)

    def test_open_dashboard_macro(self):
        with open("open_dashboard.bat", encoding="utf-8-sig") as file:
            batch = file.read()
        with open("scripts/open_dashboard.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("scripts\\open_dashboard.ps1", batch)
        self.assertIn("stock_alarm.dashboard", script)
        self.assertIn("Dashboard generation failed.", script)
        self.assertIn("Start-Process", script)

    def test_issue_alert_macro(self):
        with open("issue_alert.bat", encoding="utf-8-sig") as file:
            batch = file.read()

        self.assertIn("scripts\\run_stock_alarm.ps1", batch)
        self.assertIn("issue_alert", batch)

    def test_set_dart_key_macro(self):
        with open("set_dart_key.bat", encoding="utf-8-sig") as file:
            batch = file.read()
        with open("scripts/set_dart_key.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("scripts\\set_dart_key.ps1", batch)
        self.assertIn("STOCK_ALARM_SETENV_VALUE", script)
        self.assertIn("stock_alarm.app_setenv DART_API_KEY", script)
        self.assertIn("stock_alarm.app_setenv DART_LOOKUP 1", script)
        self.assertIn("stock_alarm.app_setenv DART_SCORE_WEIGHT $weight", script)
        self.assertIn("stock_alarm.dart_reference 005930", script)


if __name__ == "__main__":
    unittest.main()
