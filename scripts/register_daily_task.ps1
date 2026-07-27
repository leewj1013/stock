$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "scripts\run_stock_alarm.ps1"
$openAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" open"
$dailyAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" daily"
$intradayCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" intraday"

Register-ScheduledTask -TaskName "stockAlarmOpen" -Action $openAction -Trigger (New-ScheduledTaskTrigger -Daily -At 08:55) -Description "Run stockAlarm before Korean market open" -Force
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1030" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1330" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1500" -Confirm:$false -ErrorAction SilentlyContinue
schtasks.exe /Create /TN "stockAlarmIntradayEveryMinute" /TR $intradayCommand /SC DAILY /ST 09:00 /RI 1 /DU 006:30 /F | Out-Null
Register-ScheduledTask -TaskName "stockAlarmDaily" -Action $dailyAction -Trigger (New-ScheduledTaskTrigger -Daily -At 16:10) -Description "Run stockAlarm after Korean market close" -Force
