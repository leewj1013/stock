$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "scripts\run_stock_alarm.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At 16:10

Register-ScheduledTask -TaskName "stockAlarmDaily" -Action $action -Trigger $trigger -Description "Run stockAlarm after Korean market close" -Force
