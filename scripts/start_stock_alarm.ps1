$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONIOENCODING = "utf-8"

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

Write-Output "== stockAlarm startup =="
Write-Output "Registering scheduled tasks..."
& "$PSScriptRoot\register_daily_task.ps1"

Write-Output ""
Write-Output "== health =="
& $python -m stock_alarm.health

Write-Output ""
Write-Output "== task status =="
& "$PSScriptRoot\status_daily_task.ps1"

Write-Output ""
Write-Output "== daily check =="
& $python -m stock_alarm.daily_check

Write-Output ""
Write-Output "== dashboard =="
$dashboard = & $python -m stock_alarm.dashboard
Write-Output $dashboard
Start-Process -FilePath (Resolve-Path $dashboard)

Write-Output ""
Write-Output "stockAlarm startup check finished."
Write-Output "After the 08:55 open run, double-click open_check.bat."
