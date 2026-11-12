param([string]$Root = (Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive'))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Supervisor.ps1') -Root $Root

$maintenance = Test-Path -LiteralPath (Join-Path $Root 'maintenance.enabled')
$rows = @(); $failed = $false
$config = Get-Content -LiteralPath (Join-Path $Root 'components.json') -Raw | ConvertFrom-Json
foreach ($name in $config.managed_tasks) {
    try {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $decision = Get-RTXRecoveryDecision -Maintenance $maintenance -Enabled $task.Settings.Enabled -TaskState ([string]$task.State)
        if ($decision -eq 'enable_and_start') {
            Enable-ScheduledTask -TaskName $name | Out-Null
            Start-ScheduledTask -TaskName $name
        } elseif ($decision -eq 'start') { Start-ScheduledTask -TaskName $name }
        $rows += [ordered]@{task=$name;decision=$decision;state=[string]$task.State}
    } catch {
        $failed = $true
        $rows += [ordered]@{task=$name;decision='task_check_failed'}
    }
}
$state = [ordered]@{timestamp=(Get-Date).ToUniversalTime().ToString('o');maintenance=$maintenance;tasks=$rows}
$path = Join-Path $Root 'state\watchdog.json'
$temp = $path + '.' + [guid]::NewGuid().ToString('N')
try {
    [IO.File]::WriteAllText($temp, ($state | ConvertTo-Json -Depth 4 -Compress), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temp -Destination $path -Force
} finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force } }
if ($failed) { exit 1 }
