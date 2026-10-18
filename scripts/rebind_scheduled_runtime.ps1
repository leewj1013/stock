param([Parameter(Mandatory=$true)][string]$RuntimeRoot)

$ErrorActionPreference = "Stop"
$secureRoot = Join-Path $env:USERPROFILE ".stockAlarmSecure"
$resolvedRuntime = (Resolve-Path -LiteralPath $RuntimeRoot).Path
if (-not $resolvedRuntime.StartsWith($secureRoot + "\runtime-", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to bind scheduled tasks outside the protected runtime directory."
}
$pattern = [regex]::Escape($secureRoot) + '\\runtime-[^\\" ]+'
$updated = 0
foreach ($task in (Get-ScheduledTask | Where-Object { $_.TaskName -like "stockAlarm*" })) {
    $actions = @()
    $changed = $false
    foreach ($action in $task.Actions) {
        $arguments = [string]$action.Arguments
        $match = [regex]::Match($arguments, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($match.Success) {
            $arguments = $arguments.Replace($match.Value, $resolvedRuntime)
            $changed = $true
        }
        $options = @{ Execute = $action.Execute; Argument = $arguments }
        if ($action.WorkingDirectory) { $options.WorkingDirectory = $action.WorkingDirectory }
        $actions += New-ScheduledTaskAction @options
    }
    if ($changed) {
        Set-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -Action $actions | Out-Null
        $updated += 1
    }
}
Write-Output "Protected runtime task actions updated: $updated"
