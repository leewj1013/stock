$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Start-Process -FilePath "http://127.0.0.1:8765/remote-setup"
Write-Output "로컬 연결 설정 화면을 열었습니다. 토큰 복사 후 GitHub Pages 열기를 누르세요."
