$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& "$Root\.venv\Scripts\python.exe" -m stock_alarm.point_in_time_collect --sources stock_warning --dynamic-universe
exit $LASTEXITCODE
