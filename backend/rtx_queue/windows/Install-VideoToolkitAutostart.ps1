param(
    [string]$Control = 'C:\Users\Kraken\harmonika-video-toolkit-source',
    [string]$Python = 'C:\Users\Kraken\AppData\Local\Programs\Python\Python312\python.exe',
    [string]$Git = 'C:\Program Files\Git\cmd\git.exe',
    [string]$FFmpeg = 'C:\Program Files\WinGet\Links\ffmpeg.exe',
    [string]$Root = (Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive'),
    [switch]$VerifyOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$name = 'HarmonikaVideoToolkit'
foreach ($file in @($Python,$Git,$FFmpeg,(Join-Path $Control 'broker_av.py'),(Join-Path $Control 'service.token'),(Join-Path $Root 'Supervisor.ps1'),(Join-Path $Root 'ChildRuntime.py'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'video_runtime_missing' }
}
if (Test-Path -LiteralPath (Join-Path $Root 'maintenance.enabled')) { throw 'maintenance_enabled' }
$configPath = Join-Path $Root 'components.json'
$original = [IO.File]::ReadAllText($configPath)
$config = $original | ConvertFrom-Json
if ($VerifyOnly) {
    [ordered]@{ok=$true;runtime_present=$true;port=18251;existing_tasks_preserved=$true}|ConvertTo-Json -Compress
    exit 0
}
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'administrator_required' }
$backup = Join-Path $Root ('backups\video-'+(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssffffZ'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath $configPath -Destination (Join-Path $backup 'components.json')
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) { throw 'video_task_already_exists_inspect_before_change' }
$runner = Join-Path $Root 'VideoToolkit.ps1'
if (Test-Path -LiteralPath $runner) { throw 'video_runner_already_exists' }
$componentPath = [Environment]::GetEnvironmentVariable('Path','Machine')+';'+(Split-Path $Git)+';'+(Split-Path $FFmpeg)
$definition = [ordered]@{name='VideoToolkit';port=18251;executable=$Python;directory=$Control;arguments='-u broker_av.py';environment=@{PYTHONUNBUFFERED='1';HF_HUB_OFFLINE='1';TRANSFORMERS_OFFLINE='1';DO_NOT_TRACK='1';GIT_PYTHON_GIT_EXECUTABLE=$Git;PATH=$componentPath}}
$definitionPath = Join-Path $Root 'video-toolkit.json'
if (Test-Path -LiteralPath $definitionPath) { throw 'video_definition_already_exists' }
[IO.File]::WriteAllText($definitionPath,($definition|ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false)))
$script = @'
param([string]$Root = (Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive'))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $Root 'Supervisor.ps1') -Root $Root
$config = Get-Content -LiteralPath (Join-Path $Root 'video-toolkit.json') -Raw | ConvertFrom-Json
Invoke-RTXSupervisor -Configuration $config -StateRoot (Join-Path $Root 'state') -MaintenanceFile (Join-Path $Root 'maintenance.enabled')
'@
[IO.File]::WriteAllText($runner,$script,(New-Object Text.UTF8Encoding($false)))
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $powershell -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$runner+'" -Root "'+$Root+'"') -WorkingDirectory $Root
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$boot = New-ScheduledTaskTrigger -AtStartup; $boot.Delay='PT20S'
$principal = New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $name -Action $action -Settings $settings -Trigger $boot -Principal $principal -Description 'Existing private Harmonika video broker. No prompts or stdout in supervisor state.' | Out-Null
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect()
$scheduler.GetFolder('\').GetTask($name).SetSecurityDescriptor('D:P(A;;FA;;;SY)(A;;FA;;;BA)',0)
if ([IO.File]::ReadAllText($configPath) -cne $original) { throw 'runtime_config_changed' }
$config.managed_tasks=@($config.managed_tasks)+@($name)
$temporary=$configPath+'.video-next'
[IO.File]::WriteAllText($temporary,($config|ConvertTo-Json -Depth 7),(New-Object Text.UTF8Encoding($false)))
Move-Item -LiteralPath $temporary -Destination $configPath -Force
Start-ScheduledTask -TaskName $name
[ordered]@{ok=$true;task=$name;boot_without_login=$true;execution_limit='PT0S';watchdog_seconds=60;existing_processes_stopped=$false;backup_id=(Split-Path $backup -Leaf)}|ConvertTo-Json -Compress
