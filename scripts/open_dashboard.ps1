$ErrorActionPreference = "Stop"
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

$port = if ($env:DASHBOARD_PORT) { $env:DASHBOARD_PORT } else { "8765" }
$existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
if (-not $existing) {
    Start-Process -FilePath $python -ArgumentList "-m", "stock_alarm.dashboard_server" -WorkingDirectory $projectRoot -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
Start-Process -FilePath "http://127.0.0.1:$port/"
