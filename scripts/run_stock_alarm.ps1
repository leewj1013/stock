$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONIOENCODING = "utf-8"
$mode = if ($args.Count -gt 0) { $args[0] } else { "daily" }
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

if (Test-Path ".venv\Scripts\python.exe") {
    $python = ".venv\Scripts\python.exe"
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

cmd.exe /d /c "`"$python`" -m stock_alarm.run_gate $mode 1>> `"$stdout`" 2>> `"$stderr`""
if ($LASTEXITCODE -eq 2) {
    "[$(Get-Date -Format s)] SKIP $mode" | Out-File -FilePath $stdout -Append -Encoding utf8
    exit 0
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

function RunStep($name, $module) {
    "[$(Get-Date -Format s)] START $name" | Out-File -FilePath $stdout -Append -Encoding utf8
    cmd.exe /d /c "`"$python`" -m $module 1>> `"$stdout`" 2>> `"$stderr`""
    if ($LASTEXITCODE -ne 0) {
        $code = $LASTEXITCODE
        cmd.exe /d /c "`"$python`" -m stock_alarm.failure_alert $name $code 1>> `"$stdout`" 2>> `"$stderr`""
        exit $code
    }
    "[$(Get-Date -Format s)] DONE $name" | Out-File -FilePath $stdout -Append -Encoding utf8
}

function RunOptionalStep($name, $module) {
    "[$(Get-Date -Format s)] START $name" | Out-File -FilePath $stdout -Append -Encoding utf8
    cmd.exe /d /c "`"$python`" -m $module 1>> `"$stdout`" 2>> `"$stderr`""
    if ($LASTEXITCODE -ne 0) {
        $code = $LASTEXITCODE
        cmd.exe /d /c "`"$python`" -m stock_alarm.failure_alert $name $code 1>> `"$stdout`" 2>> `"$stderr`""
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
    RunFreshStep "market_summary" "stock_alarm.market_summary"
}
if ($mode -eq "intraday") {
    RunFreshStep "recommendation" "stock_alarm"
    RunFreshStep "virtual_trader_report" "stock_alarm.virtual_trader_report"
    RunStep "dashboard" "stock_alarm.dashboard"
}
if ($mode -eq "sell") {
    RunFreshStep "sell_check" "stock_alarm.sell_check"
    RunFreshStep "positions_report" "stock_alarm.positions_report"
}
if ($mode -eq "daily") {
    RunFreshOptionalStep "positions_report" "stock_alarm.positions_report"
}
if ($mode -eq "daily" -or $mode -eq "performance") {
    RunOptionalStep "recommendation_performance" "stock_alarm.recommendation_performance"
    RunOptionalStep "sell_performance" "stock_alarm.sell_performance"
}
if ($mode -eq "daily") {
    RunOptionalStep "strategy_learning" "stock_alarm.strategy_learning"
    RunStep "daily_summary" "stock_alarm.daily_summary"
    RunStep "daily_check" "stock_alarm.daily_check"
    RunStep "dashboard" "stock_alarm.dashboard"
}
if ($mode -eq "daily" -or $mode -eq "issue_alert") {
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
