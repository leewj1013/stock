$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "scripts\run_stock_alarm.ps1"
$openAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" open"
$intradayAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" intraday"
$dailyAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" daily"

Register-ScheduledTask -TaskName "stockAlarmOpen" -Action $openAction -Trigger (New-ScheduledTaskTrigger -Daily -At 08:55) -Description "Run stockAlarm before Korean market open" -Force
Register-ScheduledTask -TaskName "stockAlarmIntraday1030" -Action $intradayAction -Trigger (New-ScheduledTaskTrigger -Daily -At 10:30) -Description "Run stockAlarm intraday sell/watch check" -Force
Register-ScheduledTask -TaskName "stockAlarmIntraday1330" -Action $intradayAction -Trigger (New-ScheduledTaskTrigger -Daily -At 13:30) -Description "Run stockAlarm intraday sell/watch check" -Force
Register-ScheduledTask -TaskName "stockAlarmIntraday1500" -Action $intradayAction -Trigger (New-ScheduledTaskTrigger -Daily -At 15:00) -Description "Run stockAlarm intraday sell/watch check" -Force
Register-ScheduledTask -TaskName "stockAlarmDaily" -Action $dailyAction -Trigger (New-ScheduledTaskTrigger -Daily -At 16:10) -Description "Run stockAlarm after Korean market close" -Force
