$ErrorActionPreference='Stop'
$d=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $d
$env:PYTHONUNBUFFERED='1'
$ErrorActionPreference='Continue'
& "$d\.venv\Scripts\python.exe" -u "$d\worker.py" *> "$d\worker-runtime.log"
exit $LASTEXITCODE
