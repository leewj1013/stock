$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
# Windows PowerShell 5.1 writes `>>` redirections as UTF-16LE by default,
# which interleaved NUL-padded text with the UTF-8 START/DONE lines below.
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$runtimeRoot = Split-Path -Parent $PSScriptRoot
$workspaceMarker = Join-Path $runtimeRoot "workspace.path"
$projectRoot = if (Test-Path -LiteralPath $workspaceMarker) { (Get-Content -LiteralPath $workspaceMarker -Raw).Trim() } else { $runtimeRoot }
Set-Location $projectRoot
$env:PYTHONIOENCODING = "utf-8"
$mode = if ($args.Count -gt 0) { $args[0] } else { "daily" }
# A delayed exchange close must not hold the trading mutex while waiting.
if ($mode -eq "daily") {
    $closePython = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $closePython)) { $closePython = (Get-Command python).Source }
    $closeRunner = Join-Path $runtimeRoot "stock_alarm\isolated_runner.py"
    $closeOutput = & $closePython -I $closeRunner stock_alarm.market_sessions --close-wait-seconds
    $closeSeconds = 0
    if ([int]::TryParse([string]$closeOutput, [ref]$closeSeconds) -and $closeSeconds -gt 0 -and $closeSeconds -le 7200) {
        $closeDeadline = (Get-Date).AddSeconds($closeSeconds)
        while ((Get-Date) -lt $closeDeadline) { Start-Sleep -Seconds ([Math]::Min(30, [Math]::Max(1, [int]($closeDeadline - (Get-Date)).TotalSeconds))) }
    }
}
$executionMutex = [System.Threading.Mutex]::new($false, "stockAlarmExecutionMutex")
$mutexAcquired = $false

try {
    $mutexAcquired = $executionMutex.WaitOne([TimeSpan]::FromMinutes(3))
    if (-not $mutexAcquired) {
        throw "Another stockAlarm task is still running after 3 minutes."
    }

New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$stdout = Join-Path $projectRoot "logs\task.out.log"
$stderr = Join-Path $projectRoot "logs\task.err.log"
"" | Set-Content -Path $stderr -Encoding utf8
"`n[$(Get-Date -Format s)] MODE $mode" | Out-File -FilePath $stdout -Append -Encoding utf8

if (Test-Path (Join-Path $runtimeRoot ".venv\Scripts\python.exe")) {
    $python = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
} else {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $command) {
        throw "Python was not found. Create .venv first: python -m venv .venv"
    }
    $python = $command.Source
}
$runner = Join-Path $runtimeRoot "stock_alarm\isolated_runner.py"

function InvokeAlarmPython($module, [string[]]$moduleArguments = @()) {
    # Windows PowerShell 5.1 treats redirected native stderr as an error.
    # Let the process finish and use its exit code, even when it prints warnings.
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 1
    & $python -I $runner $module @moduleArguments 1>> $stdout 2>> $stderr
    return $LASTEXITCODE
}

$code = InvokeAlarmPython stock_alarm.run_gate @($mode)
if ($code -eq 2) {
    "[$(Get-Date -Format s)] SKIP $mode" | Out-File -FilePath $stdout -Append -Encoding utf8
    exit 0
}
if ($code -ne 0) {
    exit $code
}

function RunStep($name, $module) {
    "[$(Get-Date -Format s)] START $name" | Out-File -FilePath $stdout -Append -Encoding utf8
    $code = InvokeAlarmPython $module
    if ($code -ne 0) {
        $null = InvokeAlarmPython stock_alarm.failure_alert @($name, "$code")
        exit $code
    }
    "[$(Get-Date -Format s)] DONE $name" | Out-File -FilePath $stdout -Append -Encoding utf8
}

function RunOptionalStep($name, $module) {
    "[$(Get-Date -Format s)] START $name" | Out-File -FilePath $stdout -Append -Encoding utf8
    $code = InvokeAlarmPython $module
    if ($code -ne 0) {
        $null = InvokeAlarmPython stock_alarm.failure_alert @($name, "$code")
        "[$(Get-Date -Format s)] WARN $name exit=$code" | Out-File -FilePath $stdout -Append -Encoding utf8
        return
    }
    "[$(Get-Date -Format s)] DONE $name" | Out-File -FilePath $stdout -Append -Encoding utf8
}

function RunFreshStep($name, $module) {
    $previousNoCache = $env:NO_CACHE
    $env:NO_CACHE = "1"
    RunStep $name $module
    $env:NO_CACHE = $previousNoCache
}

function RunFreshOptionalStep($name, $module) {
    $previousNoCache = $env:NO_CACHE
    $env:NO_CACHE = "1"
    RunOptionalStep $name $module
    $env:NO_CACHE = $previousNoCache
}

if ($mode -eq "open") {
    RunOptionalStep "stock_events" "stock_alarm.stock_events"
    RunOptionalStep "premarket_valuation" "stock_alarm.premarket_valuation"
    RunFreshStep "market_summary" "stock_alarm.market_summary"
    RunOptionalStep "market_group_snapshot" "stock_alarm.market_group_snapshot"
    RunOptionalStep "naver_market_signals" "stock_alarm.naver_market_signals"
    RunOptionalStep "investment_calendar" "stock_alarm.investment_calendar"
}
if ($mode -eq "intraday") {
    RunOptionalStep "premarket_catchup" "stock_alarm.premarket_valuation"
    RunOptionalStep "stock_events" "stock_alarm.stock_events"
    RunOptionalStep "market_group_snapshot" "stock_alarm.market_group_snapshot"
    RunOptionalStep "naver_market_signals" "stock_alarm.naver_market_signals"
    RunFreshStep "recommendation" "stock_alarm"
    RunOptionalStep "minute_candle_collect" "stock_alarm.minute_candle_collect"
    RunOptionalStep "research_quote_collect" "stock_alarm.research_data_collect"
    RunOptionalStep "research_followup" "stock_alarm.research_followup"
    RunFreshStep "virtual_trader_report" "stock_alarm.virtual_trader_report"
    RunOptionalStep "market_group_context" "stock_alarm.market_group_context"
    RunOptionalStep "naver_market_signal_context" "stock_alarm.naver_market_signal_context"
    RunStep "dashboard" "stock_alarm.dashboard"
}
if ($mode -eq "sell") {
    RunFreshStep "sell_check" "stock_alarm.sell_check"
    RunFreshStep "positions_report" "stock_alarm.positions_report"
}
if ($mode -eq "daily") {
    RunFreshOptionalStep "positions_report" "stock_alarm.positions_report"
    RunOptionalStep "stock_events_close" "stock_alarm.stock_events"
    RunOptionalStep "investment_calendar_close" "stock_alarm.investment_calendar"
}
if ($mode -eq "daily" -or $mode -eq "performance") {
    RunOptionalStep "market_group_snapshot" "stock_alarm.market_group_snapshot"
    RunOptionalStep "naver_market_signals" "stock_alarm.naver_market_signals"
    RunOptionalStep "naver_market_signal_context" "stock_alarm.naver_market_signal_context"
    RunOptionalStep "recommendation_performance" "stock_alarm.recommendation_performance"
    RunOptionalStep "sell_performance" "stock_alarm.sell_performance"
    RunOptionalStep "minute_candle_collect" "stock_alarm.minute_candle_collect"
    RunOptionalStep "research_quote_collect" "stock_alarm.research_data_collect"
    RunOptionalStep "research_followup" "stock_alarm.research_followup"
}
if ($mode -eq "daily") {
    # issue_alert must still run even if a required step above fails, so the
    # day's issue summary isn't silently skipped on top of the failure itself.
    try {
        RunOptionalStep "strategy_learning" "stock_alarm.strategy_learning"
        # Screens the collected DART fundamentals and saves the result the
        # dashboard reads, so it has to run before the dashboard build below.
        RunOptionalStep "screener" "stock_alarm.screener"
        RunStep "daily_summary" "stock_alarm.daily_summary"
        RunStep "daily_check" "stock_alarm.daily_check"
        RunStep "dashboard" "stock_alarm.dashboard"
    } finally {
        RunOptionalStep "issue_alert" "stock_alarm.issue_alert"
    }
}
if ($mode -eq "issue_alert") {
    RunStep "issue_alert" "stock_alarm.issue_alert"
}
exit 0
}
finally {
    if ($mutexAcquired) {
        $executionMutex.ReleaseMutex()
    }
    $executionMutex.Dispose()
}
