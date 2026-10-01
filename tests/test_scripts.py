import unittest
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class ScriptTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows PowerShell regression")
    def test_native_stderr_does_not_abort_optional_steps(self):
        source = Path("scripts/run_stock_alarm.ps1").resolve()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / "fake.py"
            runner.write_text(
                "import sys\n"
                "module = sys.argv[1]\n"
                "if module != 'stock_alarm.daily_summary':\n"
                "    print('simulated KRX failure', file=sys.stderr)\n"
                "sys.exit(7 if module in ('stock_alarm.screener', 'stock_alarm.failure_alert') else 0)\n",
                encoding="utf-8",
            )
            def quote(value):
                return "'" + str(value).replace("'", "''") + "'"
            harness = root / "check.ps1"
            harness.write_text(
                '$ErrorActionPreference = "Stop"\n'
                "$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'\n"
                f'$ast = [System.Management.Automation.Language.Parser]::ParseFile({quote(source)}, [ref]$null, [ref]$null)\n'
                '$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $true) | ForEach-Object { Invoke-Expression $_.Extent.Text }\n'
                f'$python = {quote(sys.executable)}\n$runner = {quote(runner)}\n'
                f'$stdout = {quote(root / "out.log")}\n$stderr = {quote(root / "err.log")}\n'
                'RunStep "warning" "stock_alarm.warning"\n'
                'RunOptionalStep "screener" "stock_alarm.screener"\n'
                'RunStep "daily_summary" "stock_alarm.daily_summary"\n'
                'if ($ErrorActionPreference -ne "Stop") { throw "preference leaked" }\n'
                'RunStep "required_failure" "stock_alarm.screener"\n'
                'throw "required failure was ignored"\n', encoding="utf-8-sig",
            )
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)], capture_output=True, timeout=30)
            output = (root / "out.log").read_text(encoding="utf-8-sig")
            self.assertEqual(7, result.returncode, output + (root / "err.log").read_text(encoding="utf-8-sig"))
            self.assertIn("DONE warning", output)
            self.assertIn("WARN screener exit=7", output)
            self.assertIn("DONE daily_summary", output)
            self.assertIn("simulated KRX failure", (root / "err.log").read_text(encoding="utf-8-sig"))

    def test_screener_runs_after_market_close_and_before_the_dashboard(self):
        # The dashboard only displays the screener's saved result, so a run
        # ordered after the dashboard build would always show yesterday's list.
        with open("scripts/run_stock_alarm.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn('RunOptionalStep "screener" "stock_alarm.screener"', script)
        daily = script[script.index('RunOptionalStep "strategy_learning"'):]
        self.assertLess(daily.index('"screener"'), daily.index('RunStep "dashboard"'))

    def test_financial_statement_collection_runs_weekly_on_friday_evening(self):
        # Quarterly filings change a few times a year, so a daily 400-ticker
        # DART sweep would burn the API quota for nothing.
        with open("scripts/register_daily_task.ps1", encoding="utf-8-sig") as file:
            register = file.read()
        with open("scripts/run_financial_statements.ps1", encoding="utf-8-sig") as file:
            runner = file.read()

        self.assertIn('-TaskName "stockAlarmFinancialStatements"', register)
        self.assertIn("-Weekly -DaysOfWeek Friday -At \"19:00\"", register)
        self.assertIn("run_financial_statements.ps1", register)
        self.assertIn("stock_alarm.financial_statement_lines", runner)
        self.assertIn("--dynamic-universe --stored", runner)
        self.assertIn("workspace.path", runner)

    def test_task_scripts_write_logs_as_utf8(self):
        # PowerShell 5.1 would otherwise append python output as UTF-16LE.
        for path in ("scripts/run_stock_alarm.ps1", "scripts/run_financial_statements.ps1"):
            with open(path, encoding="utf-8-sig") as file:
                script = file.read()
            self.assertIn("$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'", script, path)
            self.assertIn("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8", script, path)

    def test_daily_task_runs_sell_check(self):
        with open("scripts/run_stock_alarm.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn('RunFreshStep "recommendation" "stock_alarm"', script)
        self.assertIn('RunFreshStep "virtual_trader_report" "stock_alarm.virtual_trader_report"', script)
        self.assertIn('RunFreshStep "market_summary" "stock_alarm.market_summary"', script)
        self.assertIn('RunFreshStep "sell_check" "stock_alarm.sell_check"', script)
        self.assertIn('RunFreshOptionalStep "positions_report" "stock_alarm.positions_report"', script)
        self.assertIn('RunOptionalStep "recommendation_performance" "stock_alarm.recommendation_performance"', script)
        self.assertIn('RunOptionalStep "sell_performance" "stock_alarm.sell_performance"', script)
        self.assertIn('RunStep "daily_check" "stock_alarm.daily_check"', script)
        self.assertIn('RunStep "dashboard" "stock_alarm.dashboard"', script)
        self.assertIn('RunStep "issue_alert" "stock_alarm.issue_alert"', script)
        self.assertIn("START $name", script)
        self.assertIn("DONE $name", script)
        self.assertIn('$mode = if ($args.Count -gt 0) { $args[0] } else { "daily" }', script)
        self.assertIn('if ($mode -eq "intraday")', script)
        self.assertIn("WARN $name exit=$code", script)
        self.assertIn('if ($mode -eq "issue_alert")', script)
        self.assertIn('RunOptionalStep "issue_alert" "stock_alarm.issue_alert"', script)
        self.assertIn("} finally {", script)
        self.assertIn("stock_alarm.failure_alert", script)
        self.assertIn("InvokeAlarmPython stock_alarm.run_gate @($mode)", script)
        self.assertIn("SKIP $mode", script)
        self.assertIn("-I $runner", script)
        self.assertIn("stock_alarm\\isolated_runner.py", script)
        self.assertIn("MODE $mode", script)
        self.assertIn("Set-Content -Path $stderr", script)
        self.assertIn('$env:NO_CACHE = "1"', script)
        self.assertIn("stockAlarmExecutionMutex", script)

    def test_register_task_adds_intraday_checks(self):
        with open("scripts/register_daily_task.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("stockAlarmOpen", script)
        self.assertIn("stockAlarmIntradayEvery5Minutes", script)
        self.assertIn("ScheduleByWeek", script)
        self.assertIn("<Monday />", script)
        self.assertIn("<Friday />", script)
        self.assertIn("Register-ScheduledTask -TaskName \"stockAlarmIntradayEvery5Minutes\" -Xml", script)
        self.assertIn("Register-ScheduledTask -TaskName \"stockAlarmSellEvery5Minutes\" -Xml", script)
        self.assertIn("'sell</Arguments>'", script)
        self.assertIn('New-ScheduledTaskAction -Execute "wscript.exe"', script)
        self.assertIn("run_powershell_hidden.vbs", script)
        self.assertIn("deploy_secure_runtime.ps1", script)
        self.assertIn("current_runtime.path", script)
        self.assertIn("-WakeToRun", script)
        self.assertIn("-StartWhenAvailable", script)
        self.assertIn("<WakeToRun>true</WakeToRun>", script)
        self.assertIn("-Daily -At 16:00", script)
        self.assertIn("<Interval>PT5M</Interval>", script)
        self.assertIn("T08:50:00", script)
        self.assertIn("<Duration>PT6H50M</Duration>", script)
        self.assertIn("Unregister-ScheduledTask -TaskName \"stockAlarmIntraday1030\"", script)
        self.assertIn("Unregister-ScheduledTask -TaskName \"stockAlarmIntraday1330\"", script)
        self.assertIn("Unregister-ScheduledTask -TaskName \"stockAlarmIntraday1500\"", script)

    def test_status_task_shows_next_run(self):
        with open("scripts/status_daily_task.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("NextRunTime", script)
        self.assertIn("next_run=", script)
        self.assertIn("yyyy-MM-dd HH:mm:ss", script)
        self.assertIn("stockAlarmIntradayEvery5Minutes", script)

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
        self.assertIn("open_dashboard.bat", script)

    def test_open_dashboard_macro(self):
        with open("open_dashboard.bat", encoding="utf-8-sig") as file:
            batch = file.read()
        with open("scripts/open_dashboard.ps1", encoding="utf-8-sig") as file:
            script = file.read()
        with open("scripts/ensure_dashboard_server.ps1", encoding="utf-8-sig") as file:
            ensure_script = file.read()

        self.assertIn("scripts\\open_dashboard.ps1", batch)
        self.assertIn("ensure_dashboard_server.ps1", script)
        self.assertIn("stock_alarm.dashboard_server", ensure_script)
        self.assertIn("http://127.0.0.1:$port/", script)
        self.assertIn("Start-Process", script)
        self.assertNotIn("Set-Clipboard", script)
        self.assertNotIn("DASHBOARD_LOCAL_USERNAME", script)
        self.assertNotIn("DASHBOARD_LOCAL_PASSWORD_HASH", script)

    def test_ensure_dashboard_server_script_checks_port_before_launching(self):
        with open("scripts/ensure_dashboard_server.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("Get-NetTCPConnection", script)
        self.assertIn("stock_alarm.dashboard_server", script)
        self.assertIn("-WindowStyle Hidden", script)

    def test_start_remote_dashboard_reuses_ensure_script(self):
        with open("scripts/start_remote_dashboard.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("ensure_dashboard_server.ps1", script)
        self.assertIn("DASHBOARD_REMOTE_PORT", script)

    def test_register_task_registers_dashboard_server_at_logon(self):
        with open("scripts/register_daily_task.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("stockAlarmDashboardServer", script)
        self.assertIn("ensure_dashboard_server.ps1", script)
        self.assertIn("<LogonTrigger>", script)

    def test_register_task_registers_daily_stock_warning_collection(self):
        with open("scripts/register_daily_task.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("stockAlarmCollectStockWarnings", script)
        self.assertIn("collect_stock_warnings.ps1", script)
        self.assertIn("Monday,Tuesday,Wednesday,Thursday,Friday", script)

    def test_collect_stock_warnings_script_runs_the_point_in_time_collector(self):
        with open("scripts/collect_stock_warnings.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("stock_alarm.point_in_time_collect", script)
        self.assertIn("--sources stock_warning", script)

    def test_register_task_registers_daily_shadow_trader(self):
        with open("scripts/register_daily_task.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("stockAlarmShadowTrader", script)
        self.assertIn("run_shadow_trader.ps1", script)

    def test_shadow_trader_script_runs_the_shadow_trader_module(self):
        with open("scripts/run_shadow_trader.ps1", encoding="utf-8-sig") as file:
            script = file.read()

        self.assertIn("stock_alarm.shadow_trader", script)

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
