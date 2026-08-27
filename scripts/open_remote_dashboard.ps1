$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$tunnelLogs = @(".\logs\cloudflared.err.log", ".\logs\cloudflared.out.log")
$content = ($tunnelLogs | ForEach-Object { Get-Content -LiteralPath $_ -Raw -ErrorAction SilentlyContinue }) -join "`n"
$match = [regex]::Match($content, "https://[a-z0-9-]+\.trycloudflare\.com")
if (-not $match.Success) {
    throw "Active tunnel URL was not found. Run scripts/start_remote_dashboard.ps1 first."
}
$tokenLine = Get-Content -LiteralPath ".\.env" | Where-Object { $_.StartsWith("DASHBOARD_REMOTE_TOKEN=") } | Select-Object -Last 1
if (-not $tokenLine) {
    throw "DASHBOARD_REMOTE_TOKEN is missing from .env"
}
$token = $tokenLine.Substring("DASHBOARD_REMOTE_TOKEN=".Length)
Set-Clipboard -Value $token
$api = [Uri]::EscapeDataString($match.Value)
$dashboardUrl = "https://leewj1013.github.io/stock/?api=$api"
Start-Process -FilePath $dashboardUrl
Write-Output "GitHub Pages를 열었고 접속 토큰을 클립보드에 복사했습니다. 토큰 입력란에 붙여넣으세요."
