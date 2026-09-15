$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$secureRoot = Join-Path $env:USERPROFILE ".stockAlarmSecure"
$runtimeRoot = Join-Path $secureRoot ("runtime-{0}-{1}" -f (Get-Date -Format "yyyyMMddHHmmss"), [Guid]::NewGuid().ToString("N").Substring(0, 8))
$account = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".venv\pyvenv.cfg"))) {
    throw "The project virtual environment is missing. Install requirements.lock first."
}
$basePythonLine = Get-Content -LiteralPath (Join-Path $projectRoot ".venv\pyvenv.cfg") -Encoding UTF8 |
    Where-Object { $_ -match '^executable\s*=\s*(.+)$' } | Select-Object -First 1
if (-not $basePythonLine) { throw "Trusted base Python path is missing from .venv\pyvenv.cfg." }
$basePython = ($basePythonLine -replace '^executable\s*=\s*', '').Trim()
if ($basePython -notmatch '^[A-Za-z]:\\' -or
    $basePython.StartsWith($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $basePython)) {
    throw "The base Python interpreter must be an existing absolute path outside the workspace."
}

New-Item -ItemType Directory -Force -Path $secureRoot | Out-Null
New-Item -ItemType Directory -Path $runtimeRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "stock_alarm") -Destination $runtimeRoot -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts") -Destination $runtimeRoot -Recurse
& $basePython -I -m venv (Join-Path $runtimeRoot ".venv")
if ($LASTEXITCODE -ne 0) { throw "Failed to create the protected virtual environment." }
& (Join-Path $runtimeRoot ".venv\Scripts\python.exe") -I -m pip install --disable-pip-version-check --require-hashes -r (Join-Path $projectRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "Failed to install locked runtime dependencies." }
Set-Content -LiteralPath (Join-Path $runtimeRoot "workspace.path") -Value $projectRoot -Encoding UTF8 -NoNewline

& icacls $runtimeRoot /inheritance:r /grant:r "${account}:(OI)(CI)(F)" "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to protect the scheduled-task runtime." }
$pointer = Join-Path $secureRoot "current_runtime.path"
Set-Content -LiteralPath $pointer -Value $runtimeRoot -Encoding UTF8 -NoNewline
& icacls $pointer /inheritance:r /grant:r "${account}:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to protect the runtime pointer." }

Write-Output "Protected scheduled-task runtime deployed. No secret values were printed."
Write-Output $runtimeRoot
