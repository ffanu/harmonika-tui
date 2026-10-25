$ErrorActionPreference='Stop'
$d=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$d\ComfyUI"
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'; $env:DO_NOT_TRACK='1'; $env:PYTHONUNBUFFERED='1'
$ErrorActionPreference='Continue'
& "$d\.venv\Scripts\python.exe" -u main.py --listen 127.0.0.1 --port 18190 --disable-auto-launch --disable-all-custom-nodes --offline --disable-metadata --lowvram --cpu-vae *> "$d\comfy-runtime.log"
exit $LASTEXITCODE
