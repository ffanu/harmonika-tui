$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$Python='C:\Users\Kraken\AppData\Local\Programs\Python\Python312\python.exe'
function Step($text) { $state=@{status='installing';phase=$text;models_ready=$false;video_generation_verified=$false;existing_flux_modified=$false}|ConvertTo-Json -Compress;[IO.File]::WriteAllText("$Root\bootstrap-status.json",$state,[Text.UTF8Encoding]::new($false));Write-Output ((Get-Date -Format o)+' '+$text) }
function Native($exe,$arguments) { $saved=$ErrorActionPreference; $ErrorActionPreference='Continue'; & $exe @arguments 2>&1 | ForEach-Object { Write-Output $_ }; $code=$LASTEXITCODE; $ErrorActionPreference=$saved; if($code -ne 0){throw 'installation_command_failed'} }
try {
 Set-Location $Root
 Step 'Preparing pinned isolated source archives'
 Native $Python @('-I','-S',"$Root\provision_source.py")
 if(!(Test-Path "$Root\.venv\Scripts\python.exe")){Step 'Creating isolated Python 3.12 environment';Native $Python @('-I','-S','-m','venv',"$Root\.venv")}
 $Py="$Root\.venv\Scripts\python.exe"
 Native $Py @('-I','-m','pip','install','--no-cache-dir','--upgrade','pip')
 Step 'Installing isolated CUDA Torch wheels (no driver changes)'
 Native $Python @('-I','-S',"$Root\install_torch.py")
 [IO.File]::WriteAllText("$Root\constraints.txt","torch==2.10.0+cu130`ntorchvision==0.25.0+cu130`ntorchaudio==2.10.0+cu130`n",[Text.UTF8Encoding]::new($false))
 Step 'Installing ComfyUI dependencies with Torch constraints'
 Native $Py @('-I','-m','pip','install','--no-cache-dir','-r',"$Root\ComfyUI\requirements.txt",'-c',"$Root\constraints.txt")
 Step 'Checking dependency consistency'
 Native $Py @('-I','-m','pip','check')
 Native $Py @('-I',"$Root\probe_environment.py")
 Step 'Base environment installed; model qualification pending'
 [IO.File]::WriteAllText("$Root\bootstrap-status.json",'{"status":"environment_installed","models_ready":false,"video_generation_verified":false,"existing_flux_modified":false}',[Text.UTF8Encoding]::new($false))
} catch {
 [IO.File]::WriteAllText("$Root\bootstrap-status.json",'{"status":"failed","models_ready":false,"video_generation_verified":false,"existing_flux_modified":false}',[Text.UTF8Encoding]::new($false))
 Write-Output ('BOOTSTRAP_FAILED '+$_.Exception.GetType().Name);exit 1
}
