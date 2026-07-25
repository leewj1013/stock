$ErrorActionPreference = "Stop"

@(
foreach ($name in "stockAlarmOpen", "stockAlarmIntraday1030", "stockAlarmIntraday1330", "stockAlarmIntraday1500", "stockAlarmDaily") {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $name

    [PSCustomObject]@{
        TaskName = $task.TaskName
        State = $task.State
        Description = $task.Description
        LastRunTime = $info.LastRunTime
        LastTaskResult = $info.LastTaskResult
        NextRunTime = $info.NextRunTime
        NumberOfMissedRuns = $info.NumberOfMissedRuns
    }
}
) | Format-Table -AutoSize
