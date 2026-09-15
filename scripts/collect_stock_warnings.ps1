$ErrorActionPreference = "Stop"
$RuntimeRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceMarker = Join-Path $RuntimeRoot "workspace.path"
$Root = if (Test-Path -LiteralPath $WorkspaceMarker) { (Get-Content -LiteralPath $WorkspaceMarker -Raw).Trim() } else { $RuntimeRoot }
Set-Location $Root
& "$RuntimeRoot\.venv\Scripts\python.exe" -I "$RuntimeRoot\stock_alarm\isolated_runner.py" stock_alarm.point_in_time_collect --sources stock_warning --dynamic-universe
exit $LASTEXITCODE
