$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

& "$PSScriptRoot\ensure_dashboard_server.ps1"

$port = if ($env:DASHBOARD_PORT) { $env:DASHBOARD_PORT } else { "8765" }
Start-Process -FilePath "http://127.0.0.1:$port/"
