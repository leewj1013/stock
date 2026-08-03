$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "scripts\run_stock_alarm.ps1"
$openAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" open"
$dailyAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" daily"
$escapedScriptPath = [System.Security.SecurityElement]::Escape($scriptPath)
$startBoundary = "$(Get-Date -Format yyyy-MM-dd)T09:00:00"
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$intradayXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>$(Get-Date -Format s)</Date>
    <Author>$env:COMPUTERNAME\$env:USERNAME</Author>
    <URI>\stockAlarmIntradayEveryMinute</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$userSid</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <Hidden>true</Hidden>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1M</Interval>
        <Duration>PT6H30M</Duration>
        <StopAtDurationEnd>true</StopAtDurationEnd>
      </Repetition>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "$escapedScriptPath" intraday</Arguments>
    </Exec>
  </Actions>
</Task>
"@

Register-ScheduledTask -TaskName "stockAlarmOpen" -Action $openAction -Trigger (New-ScheduledTaskTrigger -Daily -At 08:30) -Description "Run stockAlarm before Korean market open" -Force
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1030" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1330" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1500" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "stockAlarmIntradayEveryMinute" -Xml $intradayXml -Force | Out-Null
Register-ScheduledTask -TaskName "stockAlarmDaily" -Action $dailyAction -Trigger (New-ScheduledTaskTrigger -Daily -At 16:10) -Description "Run stockAlarm after Korean market close" -Force
