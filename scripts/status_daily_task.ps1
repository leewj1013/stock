$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName "stockAlarmDaily" -ErrorAction Stop
$info = Get-ScheduledTaskInfo -TaskName "stockAlarmDaily"

[PSCustomObject]@{
    TaskName = $task.TaskName
    State = $task.State
    Description = $task.Description
    LastRunTime = $info.LastRunTime
    LastTaskResult = $info.LastTaskResult
    NextRunTime = $info.NextRunTime
    NumberOfMissedRuns = $info.NumberOfMissedRuns
} | Format-List
