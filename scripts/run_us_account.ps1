$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$runtimeRoot = Split-Path -Parent $PSScriptRoot
$workspaceMarker = Join-Path $runtimeRoot "workspace.path"
$projectRoot = if (Test-Path -LiteralPath $workspaceMarker) { (Get-Content -LiteralPath $workspaceMarker -Raw).Trim() } else { $runtimeRoot }
Set-Location $projectRoot
$executionMutex = [System.Threading.Mutex]::new($false, "stockAlarmUSAccountMutex")
$acquired = $false
try {
    $acquired = $executionMutex.WaitOne([TimeSpan]::FromMinutes(3))
    if (-not $acquired) { throw "Another US account update is still running." }
    New-Item -ItemType Directory -Force -Path "logs" | Out-Null
    $stdout = Join-Path $projectRoot "logs\us_account.out.log"
    $stderr = Join-Path $projectRoot "logs\us_account.err.log"
    $mode = if ($args.Count -gt 0 -and $args[0] -eq "retry") { "retry" } else { "daily" }
    "[$(Get-Date -Format s)] START $mode" | Out-File -FilePath $stdout -Append -Encoding utf8
    "" | Set-Content -Path $stderr -Encoding utf8
    $python = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
    $runner = Join-Path $runtimeRoot "stock_alarm\isolated_runner.py"
    $moduleArgs = if ($mode -eq "retry") { @("--retry") } else { @() }
    $ErrorActionPreference = "Continue"
    & $python -I $runner stock_alarm.us_account_job @moduleArgs 1>> $stdout 2>> $stderr
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        if ($code -ne 2) {
            & $python -I $runner stock_alarm.failure_alert us_account "$code" 1>> $stdout 2>> $stderr
        }
        "[$(Get-Date -Format s)] FAIL $mode exit=$code" | Out-File -FilePath $stdout -Append -Encoding utf8
        exit $code
    }
    "[$(Get-Date -Format s)] DONE $mode" | Out-File -FilePath $stdout -Append -Encoding utf8
    exit 0
}
finally {
    if ($acquired) { $executionMutex.ReleaseMutex() }
    $executionMutex.Dispose()
}
