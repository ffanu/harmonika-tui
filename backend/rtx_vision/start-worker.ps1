$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$env:PYTHONUNBUFFERED='1';$env:PYTHONDONTWRITEBYTECODE='1'
Set-Location $PSScriptRoot
$ErrorActionPreference='Continue' # Native stderr is logging, not a terminating PowerShell error.
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'worker.py') *>> (Join-Path $PSScriptRoot 'worker.log')
exit $LASTEXITCODE
