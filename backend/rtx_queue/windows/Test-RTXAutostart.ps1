param([string]$InstalledRoot = (Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive'))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$fixtureRoot = Join-Path $InstalledRoot ('qualification\' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $fixtureRoot | Out-Null
$results = New-Object 'System.Collections.Generic.List[string]'
function Assert-RTX {
    param([bool]$Condition, [string]$Name)
    if (-not $Condition) { throw ('qualification_failed:' + $Name) }
    $results.Add($Name)
}

foreach ($name in @('Supervisor.ps1','Watchdog.ps1','Install-RTXAutostart.ps1','Test-RTXAutostart.ps1')) {
    $tokens=$null; $parseErrors=$null
    [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name), [ref]$tokens, [ref]$parseErrors) | Out-Null
    Assert-RTX ($parseErrors.Count -eq 0) ('powershell_parse_' + $name)
}
. (Join-Path $PSScriptRoot 'Supervisor.ps1') -Root $InstalledRoot
Assert-RTX ((Get-RTXRecoveryDecision $true $false 'Ready') -eq 'maintenance') 'maintenance_precedes_enable'
Assert-RTX ((Get-RTXRecoveryDecision $false $false 'Disabled') -eq 'enable_and_start') 'disabled_recovery'
Assert-RTX ((Get-RTXRecoveryDecision $false $true 'Ready') -eq 'start') 'stopped_recovery'
Assert-RTX ((Get-RTXRecoveryDecision $false $true 'Running') -eq 'observe') 'no_duplicate_running'
Assert-RTX ((Get-RTXRecoveryDecision $false $true 'Queued') -eq 'observe') 'no_duplicate_queued'
$listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
Assert-RTX (Test-RTXListener $port) 'existing_listener_observed'
$listener.Stop()
Assert-RTX (-not (Test-RTXListener $port)) 'closed_listener_detected'
Write-RTXMechanics $fixtureRoot 'PrivacyFixture' @{status='synthetic';pid=123;stdout='PRIVATE_SENTINEL';prompt='PRIVATE_SENTINEL';api_key='PRIVATE_SENTINEL';exception='PRIVATE_SENTINEL'}
$mechanics = Get-Content -LiteralPath (Join-Path $fixtureRoot 'PrivacyFixture.json') -Raw
Assert-RTX (-not $mechanics.Contains('PRIVATE_SENTINEL')) 'mechanics_only_state'

$config = Get-Content -LiteralPath (Join-Path $InstalledRoot 'components.json') -Raw | ConvertFrom-Json
Assert-RTX (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'ChildRuntime.py') -PathType Leaf) 'durable_child_runtime_present'
Assert-RTX (@($config.components|Where-Object{$_.name -eq 'Comfy'})[0].arguments.Contains('--disable-dynamic-vram')) 'comfy_static_memory_profile'
Assert-RTX (-not @($config.components|Where-Object{$_.name -eq 'Comfy'})[0].arguments.Contains('--lowvram')) 'text_encoder_not_forced_to_cpu'
$fixture = Join-Path $fixtureRoot 'synthetic_child.py'
# Real child failure followed by a clean exit, with no GPU or user data involved.
@'
from pathlib import Path
import sys
path=Path(__file__).with_suffix('.count')
n=int(path.read_text())+1 if path.exists() else 1
path.write_text(str(n))
print('PRIVATE_SENTINEL')
print('PRIVATE_SENTINEL',file=sys.stderr)
raise SystemExit(7 if n==1 else 0)
'@ | Set-Content -LiteralPath $fixture -Encoding ASCII
$synthetic = [pscustomobject]@{name=('Qualification-'+[guid]::NewGuid().ToString('N'));port=$port;executable=$config.components[0].executable;directory=$fixtureRoot;arguments=('-u "'+$fixture+'"');environment=[pscustomobject]@{PYTHONUNBUFFERED='1'}}
$watch = [Diagnostics.Stopwatch]::StartNew()
Invoke-RTXSupervisor -Configuration $synthetic -StateRoot $fixtureRoot -MaintenanceFile (Join-Path $fixtureRoot 'maintenance.enabled') -MaximumLaunches 2 -InitialBackoffSeconds 1
$watch.Stop()
Assert-RTX ((Get-Content -LiteralPath (Join-Path $fixtureRoot 'synthetic_child.count') -Raw).Trim() -eq '2') 'real_child_restarted_after_exit_7'
Assert-RTX ($watch.Elapsed.TotalSeconds -ge 1) 'restart_backoff_applied'
$state = Get-Content -LiteralPath (Join-Path $fixtureRoot ($synthetic.name+'.json')) -Raw | ConvertFrom-Json
Assert-RTX ($state.exit_code -eq 0 -and $state.launches -eq 2) 'clean_exit_recorded'
Assert-RTX (-not (Get-Content -LiteralPath (Join-Path $fixtureRoot ($synthetic.name+'.json')) -Raw).Contains('PRIVATE_SENTINEL')) 'child_output_not_logged'

# A maintenance guard must prevent the first launch, without stopping any process.
$synthetic.name='MaintenanceFixture'
New-Item -ItemType File -Path (Join-Path $fixtureRoot 'maintenance.enabled') | Out-Null
Invoke-RTXSupervisor -Configuration $synthetic -StateRoot $fixtureRoot -MaintenanceFile (Join-Path $fixtureRoot 'maintenance.enabled') -MaximumLaunches 1
Assert-RTX ((Get-Content -LiteralPath (Join-Path $fixtureRoot 'synthetic_child.count') -Raw).Trim() -eq '2') 'maintenance_does_not_spawn'

# Exercise real Task Scheduler disable/enable recovery on a disposable task only.
$testTask = 'HarmonikaRTXQualification-' + [guid]::NewGuid().ToString('N')
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$testAction = New-ScheduledTaskAction -Execute $powershell -Argument '-NoProfile -NonInteractive -Command "exit 0"'
$testPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
try {
    Register-ScheduledTask -TaskName $testTask -Action $testAction -Principal $testPrincipal | Out-Null
    Disable-ScheduledTask -TaskName $testTask | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $fixtureRoot 'state') | Out-Null
    [IO.File]::WriteAllText((Join-Path $fixtureRoot 'components.json'), (@{managed_tasks=@($testTask)} | ConvertTo-Json))
    & (Join-Path $PSScriptRoot 'Watchdog.ps1') -Root $fixtureRoot
    Assert-RTX (-not (Get-ScheduledTask -TaskName $testTask).Settings.Enabled) 'real_watchdog_honors_maintenance'
    Remove-Item -LiteralPath (Join-Path $fixtureRoot 'maintenance.enabled')
    & (Join-Path $PSScriptRoot 'Watchdog.ps1') -Root $fixtureRoot
    Assert-RTX (Get-ScheduledTask -TaskName $testTask).Settings.Enabled 'real_watchdog_reenables_disabled_task'
    $deadline = (Get-Date).AddSeconds(15)
    do { Start-Sleep -Milliseconds 200; $row = Get-ScheduledTask -TaskName $testTask } while ($row.State -in @('Running','Queued') -and (Get-Date) -lt $deadline)
    $testInfo = Get-ScheduledTaskInfo -TaskName $testTask
    Assert-RTX ($row.State -eq 'Ready' -and $testInfo.LastTaskResult -eq 0 -and $testInfo.LastRunTime -gt (Get-Date).AddMinutes(-1)) 'real_watchdog_starts_task_as_system'
} finally { Unregister-ScheduledTask -TaskName $testTask -Confirm:$false -ErrorAction SilentlyContinue }

foreach ($name in @($config.managed_tasks + 'HarmonikaRTXWatchdog')) {
    $task = Get-ScheduledTask -TaskName $name
    Assert-RTX ($task.Principal.UserId -eq 'SYSTEM' -or $task.Principal.UserId -eq 'S-1-5-18') ($name+'_system')
    Assert-RTX ($task.Principal.LogonType -eq 'ServiceAccount') ($name+'_no_login')
    Assert-RTX ($task.Settings.ExecutionTimeLimit -eq 'PT0S') ($name+'_unlimited')
    Assert-RTX ($task.Settings.MultipleInstances -eq 'IgnoreNew') ($name+'_singleton')
    Assert-RTX ($task.Settings.RestartCount -eq 999) ($name+'_restarts')
    Assert-RTX (-not $task.Settings.StopIfGoingOnBatteries -and -not $task.Settings.DisallowStartIfOnBatteries) ($name+'_power_conditions')
    Assert-RTX (@($task.Triggers|Where-Object{$_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger'}).Count -gt 0) ($name+'_boot_trigger')
}
$scheduler = New-Object -ComObject 'Schedule.Service'; $scheduler.Connect()
foreach ($name in @($config.managed_tasks + 'HarmonikaRTXWatchdog')) {
    $sddl = $scheduler.GetFolder('\').GetTask($name).GetSecurityDescriptor(4)
    $descriptor = New-Object Security.AccessControl.RawSecurityDescriptor($sddl)
    $allowed = @($descriptor.DiscretionaryAcl | ForEach-Object {$_.SecurityIdentifier.Value})
    Assert-RTX (@($allowed|Where-Object{$_ -notin @('S-1-5-18','S-1-5-32-544')}).Count -eq 0) ($name+'_admin_system_acl')
}
$wd = Get-ScheduledTask -TaskName 'HarmonikaRTXWatchdog'
Assert-RTX (@($wd.Triggers|Where-Object{$_.CimClass.CimClassName -eq 'MSFT_TaskTimeTrigger' -and $_.Repetition -and $_.Repetition.Interval -eq 'PT1M'}).Count -eq 1) 'watchdog_every_minute'
$out=[ordered]@{ok=$true;tests=$results.Count;checks=$results.ToArray();real_child_restart=$true;processes_killed=0;gpu_jobs_submitted=0;physical_reboot_performed=$false}
$out|ConvertTo-Json -Depth 4 -Compress
