$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "scripts\run_stock_alarm.ps1"
$hiddenLauncherPath = Join-Path $projectRoot "scripts\run_powershell_hidden.vbs"
$openAction = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$hiddenLauncherPath`" `"$scriptPath`" open"
$dailyAction = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$hiddenLauncherPath`" `"$scriptPath`" daily"
$maintenancePath = Join-Path $projectRoot "scripts\run_db_maintenance.ps1"
$maintenanceAction = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$hiddenLauncherPath`" `"$maintenancePath`""
$ensureDashboardPath = Join-Path $projectRoot "scripts\ensure_dashboard_server.ps1"
$dashboardServerAction = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$hiddenLauncherPath`" `"$ensureDashboardPath`""
$taskSettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
$escapedScriptPath = [System.Security.SecurityElement]::Escape($scriptPath)
$escapedHiddenLauncherPath = [System.Security.SecurityElement]::Escape($hiddenLauncherPath)
$startBoundary = "$(Get-Date -Format yyyy-MM-dd)T08:50:00"
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$intradayXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>$(Get-Date -Format s)</Date>
    <Author>$env:COMPUTERNAME\$env:USERNAME</Author>
    <URI>\stockAlarmIntradayEvery5Minutes</URI>
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
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <Hidden>true</Hidden>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
      <Interval>PT5M</Interval>
        <Duration>PT6H50M</Duration>
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
      <Command>wscript.exe</Command>
      <Arguments>"$escapedHiddenLauncherPath" "$escapedScriptPath" intraday</Arguments>
    </Exec>
  </Actions>
</Task>
"@
# Offset by 2 minutes so the two 5-minute tasks don't fire at the same instant
# and contend for run_stock_alarm.ps1's execution mutex every single cycle.
$sellStartBoundary = $startBoundary.Replace("08:50:00", "08:52:00")
$sellXml = $intradayXml.Replace("stockAlarmIntradayEvery5Minutes", "stockAlarmSellEvery5Minutes").Replace('intraday</Arguments>', 'sell</Arguments>').Replace($startBoundary, $sellStartBoundary)

Register-ScheduledTask -TaskName "stockAlarmOpen" -Action $openAction -Trigger (New-ScheduledTaskTrigger -Daily -At 08:30) -Settings $taskSettings -Description "Run stockAlarm before Korean market open" -Force
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1030" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1330" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "stockAlarmIntraday1500" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "stockAlarmIntradayEveryMinute" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "stockAlarmIntradayEvery5Minutes" -Xml $intradayXml -Force | Out-Null
Register-ScheduledTask -TaskName "stockAlarmSellEvery5Minutes" -Xml $sellXml -Force | Out-Null
Register-ScheduledTask -TaskName "stockAlarmDaily" -Action $dailyAction -Trigger (New-ScheduledTaskTrigger -Daily -At 16:00) -Settings $taskSettings -Description "Run stockAlarm after Korean market close" -Force
Register-ScheduledTask -TaskName "stockAlarmMaintenance" -Action $maintenanceAction -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 18:00) -Settings $taskSettings -Description "Verify and back up the stockAlarm database" -Force
# ensure_dashboard_server.ps1 is a no-op once the port is already listening,
# so re-checking it periodically is a cheap way to revive the server if it
# was ever stopped (manually, by a crash, or by sleep) between logons --
# AtLogOn alone only fires once per session and never recovers after that.
# Limited to weekday market hours (like the intraday/sell tasks) instead of
# every 15 minutes around the clock, since that's the only time it's actually
# useful and it cuts down how often the hidden-launcher window briefly flashes.
$escapedEnsureDashboardPath = [System.Security.SecurityElement]::Escape($ensureDashboardPath)
$dashboardServerXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>$(Get-Date -Format s)</Date>
    <Author>$env:COMPUTERNAME\$env:USERNAME</Author>
    <URI>\stockAlarmDashboardServer</URI>
    <Description>Start the local dashboard API server at logon and re-check every 15 minutes during market hours so it self-heals if stopped</Description>
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
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <Hidden>true</Hidden>
  </Settings>
  <Triggers>
    <LogonTrigger>
      <UserId>$userSid</UserId>
    </LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>$($startBoundary.Replace("08:50:00", "08:51:00"))</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT15M</Interval>
        <Duration>PT6H50M</Duration>
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
      <Command>wscript.exe</Command>
      <Arguments>"$escapedHiddenLauncherPath" "$escapedEnsureDashboardPath"</Arguments>
    </Exec>
  </Actions>
</Task>
"@
Register-ScheduledTask -TaskName "stockAlarmDashboardServer" -Xml $dashboardServerXml -Force | Out-Null
