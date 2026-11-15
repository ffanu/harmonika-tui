param(
    [Parameter(Mandatory=$true)][string]$ApplicationRoot,
    [Parameter(Mandatory=$true)][string]$ModelRoot,
    [string]$Root = (Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive'),
    [switch]$VerifyOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if(-not $admin){throw 'administrator_required'}
if(-not (Test-Path -LiteralPath $Root -PathType Container)){throw 'private_supervisor_root_required'}
$spec=@(
    @{folder='text_encoders';name='t5xxl_fp8_e4m3fn.safetensors'},
    @{folder='text_encoders';name='qwen_3_4b_fp4_flux2.safetensors'},
    @{folder='text_encoders';name='qwen_2.5_vl_7b_fp8_scaled.safetensors'},
    @{folder='vae';name='flux-vae-bf16.safetensors'},
    @{folder='vae';name='flux2-vae.safetensors'},
    @{folder='vae';name='qwen_image_vae.safetensors'}
)
$models=Join-Path $ApplicationRoot 'harmonika-flux-worker\ComfyUI\models'
$rows=@()
foreach($item in $spec){
    $source=Join-Path (Join-Path $ModelRoot $item.folder) $item.name
    $target=Join-Path (Join-Path $models $item.folder) $item.name
    if(-not (Test-Path -LiteralPath $source -PathType Leaf) -or (Get-Item -LiteralPath $source).Length -lt 1000000){throw 'model_source_missing'}
    $existing=Get-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    if($existing -and -not (Test-Path -LiteralPath $target -PathType Leaf) -and $existing.LinkType -ne 'SymbolicLink'){throw 'non_link_target_conflict'}
    $rows+=@{name=$item.name;source=$source;target=$target;ready=(Test-Path -LiteralPath $target -PathType Leaf)}
}
if($VerifyOnly){[ordered]@{ok=$true;existing_sources=$rows.Count;missing_references=@($rows|Where-Object{-not $_.ready}|ForEach-Object{$_.name})}|ConvertTo-Json -Compress;exit 0}
$backup=Join-Path $Root ('backups\models-'+(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssffffZ'))
New-Item -ItemType Directory -Path $backup|Out-Null
$changes=@()
try{
    foreach($row in $rows){
        if($row.ready){continue}
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $row.target)|Out-Null
        $old=Get-Item -LiteralPath $row.target -Force -ErrorAction SilentlyContinue
        $saved=$null
        if($old){$saved=Join-Path $backup ($row.name+'.previous-link');Move-Item -LiteralPath $row.target -Destination $saved}
        $record=@{name=$row.name;target=$row.target;source=$row.source;saved=$saved;created=$false}
        $changes+=$record
        New-Item -ItemType SymbolicLink -Path $row.target -Target $row.source|Out-Null
        $record.created=$true
        if(-not (Test-Path -LiteralPath $row.target -PathType Leaf)){throw 'model_link_unreadable'}
    }
    [IO.File]::WriteAllText((Join-Path $backup 'references.json'),($changes|ConvertTo-Json -Depth 3))
}catch{
    foreach($record in $changes){
        if($record.created){[IO.File]::Delete($record.target)}
        if($record.saved){Move-Item -LiteralPath $record.saved -Destination $record.target}
    }
    throw 'model_reference_repair_failed'
}
[ordered]@{ok=$true;links_created=$changes.Count;models_downloaded=0;weights_moved=0;running_processes_stopped=0;model_names=@($rows|ForEach-Object{$_.name})}|ConvertTo-Json -Depth 3 -Compress
