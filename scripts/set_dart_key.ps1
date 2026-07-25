$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONIOENCODING = "utf-8"

$key = Read-Host "OpenDART API key"
if (-not $key) {
    throw "OpenDART API key is empty."
}

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

$env:STOCK_ALARM_SETENV_VALUE = $key
& $python -m stock_alarm.app_setenv DART_API_KEY
Remove-Item Env:\STOCK_ALARM_SETENV_VALUE -ErrorAction SilentlyContinue
& $python -m stock_alarm.app_setenv DART_LOOKUP 1
& $python -m stock_alarm.dart_reference 005930
