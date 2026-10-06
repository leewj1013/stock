$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONIOENCODING = "utf-8"

function Read-Secret([string]$prompt) {
    $secure = Read-Host $prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$python = ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python was not found. Create .venv first: python -m venv .venv"
}

# Input is hidden; values go straight to the protected store, never to the screen or logs.
foreach ($name in "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET") {
    $value = Read-Secret $name
    if (-not $value) {
        throw "$name is empty."
    }
    $env:STOCK_ALARM_SETENV_VALUE = $value
    try { & $python -m stock_alarm.app_setenv $name }
    finally { Remove-Item Env:\STOCK_ALARM_SETENV_VALUE -ErrorAction SilentlyContinue }
    if ($LASTEXITCODE -ne 0) { throw "saving $name failed." }
    Write-Host "$name saved."
}

Write-Host "Checking the Toss connection..."
& $python -m stock_alarm.toss_check
