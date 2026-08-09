$ErrorActionPreference = "Stop"

$rows = @(
foreach ($name in "stockAlarmOpen", "stockAlarmIntradayEvery5Minutes", "stockAlarmDaily", "stockAlarmMaintenance") {
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
    Write-Output "next_run=$($next.TaskName) at $($next.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss'))"
}
