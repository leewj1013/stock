$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$cloudflared = Join-Path $projectRoot ".tools\cloudflared.exe"
if (-not (Test-Path -LiteralPath $cloudflared)) {
    throw "cloudflared is missing: $cloudflared"
}

$port = 8765
$listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "-m", "stock_alarm.dashboard_server" -WorkingDirectory $projectRoot -WindowStyle Hidden
    Start-Sleep -Seconds 2
}

New-Item -ItemType Directory -Force -Path ".\logs" | Out-Null
$stdout = Join-Path $projectRoot "logs\cloudflared.out.log"
$stderr = Join-Path $projectRoot "logs\cloudflared.err.log"
$process = Start-Process -FilePath $cloudflared -ArgumentList "tunnel", "--url", "http://127.0.0.1:$port", "--no-autoupdate" -WorkingDirectory $projectRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
Set-Content -LiteralPath ".\logs\cloudflared.pid" -Value $process.Id -Encoding ascii

$url = ""
for ($attempt = 0; $attempt -lt 20 -and -not $url; $attempt++) {
    Start-Sleep -Seconds 1
    $content = (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue) + (Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue)
    $match = [regex]::Match($content, "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($match.Success) { $url = $match.Value }
}
if (-not $url) {
    throw "Tunnel URL was not created. Check logs/cloudflared.err.log"
}
Write-Output "REMOTE_DASHBOARD_API=$url"
Write-Output "GitHub Pages에서 이 주소와 .env의 DASHBOARD_REMOTE_TOKEN을 입력하세요."
