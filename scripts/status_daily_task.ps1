$ErrorActionPreference = "Stop"

$rows = @(
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
)
$rows | Format-Table -AutoSize
$next = $rows | Where-Object { $_.NextRunTime -gt (Get-Date) } | Sort-Object NextRunTime | Select-Object -First 1
if ($next) {
    Write-Output "next_run=$($next.TaskName) at $($next.NextRunTime)"
}
