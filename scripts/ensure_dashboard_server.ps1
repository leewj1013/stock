$ErrorActionPreference = "Stop"
$runtimeRoot = Split-Path -Parent $PSScriptRoot
$workspaceMarker = Join-Path $runtimeRoot "workspace.path"
$projectRoot = if (Test-Path -LiteralPath $workspaceMarker) { (Get-Content -LiteralPath $workspaceMarker -Raw).Trim() } else { $runtimeRoot }
Set-Location $projectRoot
$env:PYTHONIOENCODING = "utf-8"

if (Test-Path (Join-Path $runtimeRoot ".venv\Scripts\pythonw.exe")) {
    $python = Join-Path $runtimeRoot ".venv\Scripts\pythonw.exe"
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

$port = if ($env:DASHBOARD_PORT) { $env:DASHBOARD_PORT } else { "8765" }
$existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
if (-not $existing) {
    Start-Process -FilePath $python -ArgumentList "-I", $runner, "stock_alarm.dashboard_server" -WorkingDirectory $projectRoot -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
