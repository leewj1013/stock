$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$port = if ($env:DASHBOARD_V2_PORT) { [int]$env:DASHBOARD_V2_PORT } else { 8767 }
$python = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Python 가상환경을 찾을 수 없습니다: $python" }
$listening = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process -FilePath $python -ArgumentList "-m", "stock_alarm.dashboard_v2_server" -WorkingDirectory $projectRoot -WindowStyle Hidden
}
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/v2" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { break }
    } catch {
        Start-Sleep -Milliseconds 400
    }
}
if ($attempt -eq 15) { throw "v2 서버가 시작되지 않았습니다. 포트 $port 연결 상태를 확인해 주세요." }
Start-Process -FilePath "http://127.0.0.1:$port/v2"
