param(
    [string]$Root=(Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive'),
    [string]$Control='C:\Users\Kraken\harmonika-high-video',
    [string]$Python='C:\Users\Kraken\AppData\Local\Programs\Python\Python312\python.exe'
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$name='HarmonikaHighVideo'
$comfyName='HarmonikaHighVideoComfy'
foreach($file in @($Python,(Join-Path $Control 'broker.py'),(Join-Path $Control 'runner.py'),(Join-Path $Root 'Supervisor.ps1'),(Join-Path $Root 'ChildRuntime.py'))){
    if(-not(Test-Path -LiteralPath $file -PathType Leaf)){throw 'high_video_runtime_missing'}
}
$comfyRoot='C:\Users\Kraken\harmonika-flux-worker\ComfyUI'
$comfyPython='C:\Users\Kraken\harmonika-flux-worker\.venv\Scripts\python.exe'
foreach($file in @($comfyPython,(Join-Path $comfyRoot 'main.py'),(Join-Path $Control 'service.token'),(Join-Path $Control 'models-receipt.json'))){
    if(-not(Test-Path -LiteralPath $file -PathType Leaf)){throw 'high_video_dependency_missing'}
}
foreach($file in @('HighVideo.ps1','HighVideoComfy.ps1','high-video.json','high-video-comfy.json')){
    if(Test-Path -LiteralPath (Join-Path $Root $file)){throw 'high_video_configuration_exists'}
}
if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw 'high_video_task_exists'}
if(Get-ScheduledTask -TaskName $comfyName -ErrorAction SilentlyContinue){throw 'high_video_comfy_task_exists'}
if(Get-NetTCPConnection -LocalPort 18261 -State Listen -ErrorAction SilentlyContinue){throw 'high_video_port_busy'}
if(Get-NetTCPConnection -LocalPort 18193 -State Listen -ErrorAction SilentlyContinue){throw 'high_video_comfy_port_busy'}
if(Test-Path -LiteralPath (Join-Path $Root 'maintenance.enabled')){throw 'maintenance_enabled'}
$backup=Join-Path $Root ('backups\high-video-'+(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssffffZ'))
New-Item -ItemType Directory -Path $backup|Out-Null
$configPath=Join-Path $Root 'components.json';$before=[IO.File]::ReadAllText($configPath)
Copy-Item -LiteralPath $configPath -Destination (Join-Path $backup 'components.json')
$definition=[ordered]@{name='HighVideo';port=18261;executable=$Python;directory=$Control;arguments='-u broker.py';environment=@{PYTHONUNBUFFERED='1';HF_HUB_OFFLINE='1';DO_NOT_TRACK='1'}}
[IO.File]::WriteAllText((Join-Path $Root 'high-video.json'),($definition|ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false)))
$runner=Join-Path $Root 'HighVideo.ps1'
if(Test-Path -LiteralPath $runner){throw 'high_video_runner_exists'}
$script=@'
param([string]$Root=(Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive'))
$ErrorActionPreference='Stop'
. (Join-Path $Root 'Supervisor.ps1') -Root $Root
$config=Get-Content -LiteralPath (Join-Path $Root 'high-video.json') -Raw|ConvertFrom-Json
Invoke-RTXSupervisor -Configuration $config -StateRoot (Join-Path $Root 'state') -MaintenanceFile (Join-Path $Root 'maintenance.enabled')
'@
[IO.File]::WriteAllText($runner,$script,(New-Object Text.UTF8Encoding($false)))
$action=New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$runner+'"') -WorkingDirectory $Root
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$boot=New-ScheduledTaskTrigger -AtStartup;$boot.Delay='PT25S'
$principal=New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $name -Action $action -Settings $settings -Trigger $boot -Principal $principal -Description 'Qualified native-resolution Wan 2.2 video broker; existing GPU services preserved.'|Out-Null
$data='C:\HarmonikaVideo\quality-comfy'
foreach($dir in @('input','output','temp','user')){New-Item -ItemType Directory -Force (Join-Path $data $dir)|Out-Null}
$comfyDefinition=[ordered]@{name='HighVideoComfy';port=18193;executable=$comfyPython;directory=$comfyRoot;arguments=('-u main.py --listen 127.0.0.1 --port 18193 --lowvram --reserve-vram 2 --disable-metadata --disable-auto-launch --disable-all-custom-nodes --output-directory "'+$data+'\output" --input-directory "'+$data+'\input" --temp-directory "'+$data+'\temp" --user-directory "'+$data+'\user"');environment=@{PYTHONUNBUFFERED='1';HF_HUB_OFFLINE='1';DO_NOT_TRACK='1'}}
[IO.File]::WriteAllText((Join-Path $Root 'high-video-comfy.json'),($comfyDefinition|ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false)))
$comfyRunner=Join-Path $Root 'HighVideoComfy.ps1'
if(Test-Path -LiteralPath $comfyRunner){throw 'high_video_comfy_runner_exists'}
$comfyScript=$script.Replace('high-video.json','high-video-comfy.json')
[IO.File]::WriteAllText($comfyRunner,$comfyScript,(New-Object Text.UTF8Encoding($false)))
$comfyAction=New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$comfyRunner+'"') -WorkingDirectory $Root
Register-ScheduledTask -TaskName $comfyName -Action $comfyAction -Settings $settings -Trigger $boot -Principal $principal -Description 'Dedicated Wan 2.2 runtime with GPU VAE; existing ComfyUI remains untouched.'|Out-Null
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect()
$scheduler.GetFolder('\').GetTask($name).SetSecurityDescriptor('D:P(A;;FA;;;SY)(A;;FA;;;BA)',0)
$scheduler.GetFolder('\').GetTask($comfyName).SetSecurityDescriptor('D:P(A;;FA;;;SY)(A;;FA;;;BA)',0)
if([IO.File]::ReadAllText($configPath) -cne $before){throw 'runtime_config_changed'}
$config=$before|ConvertFrom-Json;$config.managed_tasks=@($config.managed_tasks)+@($name,$comfyName)
$temporary=$configPath+'.high-video-next'
[IO.File]::WriteAllText($temporary,($config|ConvertTo-Json -Depth 7),(New-Object Text.UTF8Encoding($false)))
Move-Item -LiteralPath $temporary -Destination $configPath -Force
Start-ScheduledTask -TaskName $comfyName
Start-ScheduledTask -TaskName $name
[ordered]@{ok=$true;task=$name;boot_without_login=$true;port=18261;existing_processes_stopped=$false}|ConvertTo-Json -Compress
