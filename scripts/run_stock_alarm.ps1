$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONIOENCODING = "utf-8"
$mode = if ($args.Count -gt 0) { $args[0] } else { "daily" }

New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$stdout = Join-Path $projectRoot "logs\task.out.log"
$stderr = Join-Path $projectRoot "logs\task.err.log"

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

function RunStep($name, $module) {
    "[$(Get-Date -Format s)] START $name" | Out-File -FilePath $stdout -Append -Encoding utf8
    & $python -m $module 1>> $stdout 2>> $stderr
    if ($LASTEXITCODE -ne 0) {
        $code = $LASTEXITCODE
        & $python -m stock_alarm.failure_alert $name $code 1>> $stdout 2>> $stderr
        exit $code
    }
    "[$(Get-Date -Format s)] DONE $name" | Out-File -FilePath $stdout -Append -Encoding utf8
}

if ($mode -eq "daily" -or $mode -eq "open") {
    RunStep "recommendation" "stock_alarm"
}
if ($mode -eq "daily" -or $mode -eq "intraday") {
    RunStep "sell_check" "stock_alarm.sell_check"
    RunStep "positions_report" "stock_alarm.positions_report"
}
if ($mode -eq "daily" -or $mode -eq "performance") {
    RunStep "recommendation_performance" "stock_alarm.recommendation_performance"
}
if ($mode -eq "daily") {
    RunStep "daily_summary" "stock_alarm.daily_summary"
    RunStep "daily_check" "stock_alarm.daily_check"
    RunStep "dashboard" "stock_alarm.dashboard"
}
if ($mode -eq "daily" -or $mode -eq "issue_alert") {
    RunStep "issue_alert" "stock_alarm.issue_alert"
}
exit 0
