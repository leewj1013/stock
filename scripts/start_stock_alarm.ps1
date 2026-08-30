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
Write-Output "== dashboard api server =="
$port = if ($env:DASHBOARD_PORT) { $env:DASHBOARD_PORT } else { "8765" }
$listening = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process -FilePath $python -ArgumentList "-m", "stock_alarm.dashboard_server" -WorkingDirectory $projectRoot -WindowStyle Hidden
    Start-Sleep -Seconds 2
    $listening = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
}
if ($listening) {
    Write-Output "Dashboard API server is listening on port $port."
} else {
    Write-Output "WARNING: Dashboard API server did not start on port $port."
    Write-Output "Run '$python -m stock_alarm.dashboard_server' manually to see the error."
}

Write-Output ""
Write-Output "== dashboard =="
$dashboard = & $python -m stock_alarm.dashboard
Write-Output $dashboard
Start-Process -FilePath (Resolve-Path $dashboard)

Write-Output ""
Write-Output "stockAlarm startup check finished."
Write-Output "Use open_dashboard.bat to refresh and open the dashboard."
