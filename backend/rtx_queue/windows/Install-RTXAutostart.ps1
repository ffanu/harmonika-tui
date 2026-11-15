param(
    [Parameter(Mandatory=$true)][string]$ApplicationRoot,
    [string]$Root = (Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive'),
    [switch]$VerifyOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'administrator_required' }
if (Test-Path -LiteralPath (Join-Path $Root 'maintenance.enabled')) { throw 'maintenance_enabled' }
foreach ($name in @('Supervisor.ps1','Watchdog.ps1','ChildRuntime.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) { throw 'supervisor_runtime_missing' }
}
$python = Join-Path $ApplicationRoot 'harmonika-flux-worker\.venv\Scripts\python.exe'
$gateway = Join-Path $ApplicationRoot 'harmonika-gateway'
$comfy = Join-Path $ApplicationRoot 'harmonika-flux-worker\ComfyUI'
foreach ($file in @($python,(Join-Path $gateway 'gateway.py'),(Join-Path $comfy 'main.py'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'runtime_missing' }
}
$gatewayConfig = Get-Content -LiteralPath (Join-Path $gateway 'config.json') -Raw | ConvertFrom-Json
if ($gatewayConfig.port -ne 18200) { throw 'unexpected_gateway_port' }
$names = @('HarmonikaGateway','HarmonikaComfy','HarmonikaFluxWorker','HarmonikaRTXImageWorkerAuto')
foreach ($name in $names) { Get-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null }
if ($VerifyOnly) { [ordered]@{ok=$true;administrator=$admin;runtime_files_present=$true;task_count=$names.Count}|ConvertTo-Json -Compress; exit 0 }

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssffffZ')
$backup = Join-Path $Root ('backups\'+$stamp)
New-Item -ItemType Directory -Force -Path $backup | Out-Null
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
    $id = New-Object Security.Principal.SecurityIdentifier($sid)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($id,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $Root -AclObject $acl
foreach ($name in $names) {
    Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath (Join-Path $backup ($name+'.xml')) -Encoding UTF8
}
$watchdogExists = Get-ScheduledTask -TaskName 'HarmonikaRTXWatchdog' -ErrorAction SilentlyContinue
if ($watchdogExists) { Export-ScheduledTask -TaskName 'HarmonikaRTXWatchdog'|Set-Content -LiteralPath (Join-Path $backup 'HarmonikaRTXWatchdog.xml') -Encoding UTF8 }
foreach ($name in @('components.json','Supervisor.ps1','Watchdog.ps1','ChildRuntime.py')) {
    if (Test-Path -LiteralPath (Join-Path $Root $name)) { Copy-Item -LiteralPath (Join-Path $Root $name) -Destination (Join-Path $backup $name) }
}
foreach ($name in @('Supervisor.ps1','Watchdog.ps1','ChildRuntime.py')) {
    if ((Join-Path $PSScriptRoot $name) -ne (Join-Path $Root $name)) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $Root $name) -Force }
}
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'state') | Out-Null
$config = [ordered]@{schema_version=1;managed_tasks=$names;components=@(
    [ordered]@{name='Gateway';port=18200;executable=$python;directory=$gateway;arguments='-u gateway.py';environment=@{PYTHONUNBUFFERED='1'}},
    [ordered]@{name='Comfy';port=18190;executable=$python;directory=$comfy;arguments='-u main.py --listen 127.0.0.1 --port 18190 --disable-auto-launch --disable-all-custom-nodes --offline --disable-metadata --disable-dynamic-vram --reserve-vram 2 --vram-headroom 2 --disable-smart-memory --cpu-vae';environment=@{HF_HUB_OFFLINE='1';TRANSFORMERS_OFFLINE='1';DO_NOT_TRACK='1';PYTHONUNBUFFERED='1'}}
)}
[IO.File]::WriteAllText((Join-Path $Root 'components.json'),($config|ConvertTo-Json -Depth 7),(New-Object Text.UTF8Encoding($false)))
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun
$boot = New-ScheduledTaskTrigger -AtStartup; $boot.Delay = 'PT20S'
foreach ($name in $names) {
    $parameters = @{TaskName=$name;Settings=$settings;Principal=$principal;Trigger=@($boot)}
    if ($name -in @('HarmonikaGateway','HarmonikaComfy')) {
        $component = if ($name -eq 'HarmonikaGateway') {'Gateway'} else {'Comfy'}
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $Root 'Supervisor.ps1')+'" -Component '+$component+' -Root "'+$Root+'"'
        $parameters.Action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $Root
    }
    # Updating a running task changes future launches; it does not kill its process.
    Set-ScheduledTask @parameters | Out-Null
    Enable-ScheduledTask -TaskName $name | Out-Null
}
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$watchAction = New-ScheduledTaskAction -Execute $powershell -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $Root 'Watchdog.ps1')+'" -Root "'+$Root+'"') -WorkingDirectory $Root
Register-ScheduledTask -TaskName 'HarmonikaRTXWatchdog' -Action $watchAction -Trigger @($boot,$repeat) -Settings $settings -Principal $principal -Description 'Harmonika RTX recovery. Administrator maintenance flag is supported; mechanics only.' -Force | Out-Null
$scheduler = New-Object -ComObject 'Schedule.Service'; $scheduler.Connect(); $folder = $scheduler.GetFolder('\')
foreach ($name in @($names + 'HarmonikaRTXWatchdog')) {
    # Documented task security descriptor, normal SYSTEM/Administrator control.
    $folder.GetTask($name).SetSecurityDescriptor('D:P(A;;FA;;;SY)(A;;FA;;;BA)',0)
}
Start-ScheduledTask -TaskName 'HarmonikaRTXWatchdog'
[ordered]@{ok=$true;backup_id=$stamp;boot_without_login=$true;execution_limit='PT0S';watchdog_seconds=60;restart_count=999;normal_users_can_modify=$false;administrator_maintenance_supported=$true;existing_processes_stopped=$false}|ConvertTo-Json -Compress
