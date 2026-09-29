$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
# Windows PowerShell 5.1 writes `>>` redirections as UTF-16LE by default.
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$RuntimeRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceMarker = Join-Path $RuntimeRoot "workspace.path"
$Root = if (Test-Path -LiteralPath $WorkspaceMarker) { (Get-Content -LiteralPath $WorkspaceMarker -Raw).Trim() } else { $RuntimeRoot }
Set-Location $Root
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = Join-Path $Root "logs\consensus.log"
"[$(Get-Date -Format s)] START consensus_collect" | Out-File -FilePath $log -Append -Encoding utf8
& "$RuntimeRoot\.venv\Scripts\python.exe" -I "$RuntimeRoot\stock_alarm\isolated_runner.py" stock_alarm.consensus_collect 1>> $log 2>&1
"[$(Get-Date -Format s)] DONE consensus_collect exit=$LASTEXITCODE" | Out-File -FilePath $log -Append -Encoding utf8
exit $LASTEXITCODE
