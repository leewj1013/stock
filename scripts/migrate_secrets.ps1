$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourcePath = Join-Path $projectRoot ".env"
$secureDirectory = Join-Path $env:USERPROFILE ".stockAlarmSecure"
$securePath = Join-Path $secureDirectory "secrets.env"
$legacySecurePath = Join-Path $env:LOCALAPPDATA "stockAlarm\secrets.env"

$sensitiveKeys = @(
    "DART_API_KEY", "DASHBOARD_LOCAL_TOKEN",
    "DASHBOARD_LOCAL_USERNAME", "DASHBOARD_LOCAL_PASSWORD_HASH",
    "DASHBOARD_REMOTE_TOKEN", "KAKAO_ACCESS_TOKEN", "KAKAO_JAVASCRIPT_KEY",
    "KAKAO_NATIVE_APP_KEY", "KAKAO_REFRESH_TOKEN", "KAKAO_REST_API_KEY",
    "KRX_API_KEY", "KRX_ID", "KRX_PW", "NAVER_ACCESS_KEY_ID",
    "NAVER_HUB_CLIENT_ID", "NAVER_HUB_CLIENT_SECRET", "NAVER_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TOSS_CLIENT_ID",
    "TOSS_CLIENT_SECRET"
)
$sensitive = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
foreach ($key in $sensitiveKeys) { [void]$sensitive.Add($key) }
$obsoleteLocalDashboardKeys = @{
    "DASHBOARD_LOCAL_TOKEN" = $true
    "DASHBOARD_LOCAL_USERNAME" = $true
    "DASHBOARD_LOCAL_PASSWORD_HASH" = $true
}

New-Item -ItemType Directory -Force -Path $secureDirectory | Out-Null
$values = [ordered]@{}
$existingSecurePath = if (Test-Path -LiteralPath $securePath) { $securePath } elseif (Test-Path -LiteralPath $legacySecurePath) { $legacySecurePath } else { $null }
if ($existingSecurePath) {
    foreach ($line in Get-Content -LiteralPath $existingSecurePath -Encoding UTF8) {
        if ($line -match '^\s*([^#=]+)=(.*)$') {
            $key = $matches[1].Trim()
            if (-not $obsoleteLocalDashboardKeys.ContainsKey($key)) { $values[$key] = $matches[2] }
        }
    }
}

$publicLines = [System.Collections.Generic.List[string]]::new()
if (Test-Path -LiteralPath $sourcePath) {
    foreach ($line in Get-Content -LiteralPath $sourcePath -Encoding UTF8) {
        if ($line -match '^\s*([^#=]+)=(.*)$' -and $sensitive.Contains($matches[1].Trim())) {
            $key = $matches[1].Trim()
            if (-not $obsoleteLocalDashboardKeys.ContainsKey($key)) { $values[$key] = $matches[2] }
        } else {
            $publicLines.Add($line)
        }
    }
}

$secureTemporary = Join-Path $secureDirectory ("secrets-{0}.tmp" -f [Guid]::NewGuid().ToString("N"))
$values.GetEnumerator() | ForEach-Object { "{0}={1}" -f $_.Key, $_.Value } |
    Set-Content -LiteralPath $secureTemporary -Encoding UTF8

$account = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
& icacls $secureDirectory /inheritance:r /grant:r "${account}:(OI)(CI)(F)" "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to protect the secure credential directory." }
& icacls $secureTemporary /inheritance:r /grant:r "${account}:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to protect the secure credential file." }
Move-Item -LiteralPath $secureTemporary -Destination $securePath -Force

if (Test-Path -LiteralPath $sourcePath) {
    $publicTemporary = Join-Path $projectRoot (".env-{0}.tmp" -f [Guid]::NewGuid().ToString("N"))
    $publicLines | Set-Content -LiteralPath $publicTemporary -Encoding UTF8
    Move-Item -LiteralPath $publicTemporary -Destination $sourcePath -Force
}

Write-Output "Credentials migrated to the protected per-user store. No secret values were printed."
