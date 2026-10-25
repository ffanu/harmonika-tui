$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$env:OLLAMA_HOST='127.0.0.1:11439'
$env:OLLAMA_MODELS=Join-Path $PSScriptRoot 'models'
$env:OLLAMA_NO_CLOUD='1'
$env:OLLAMA_NOHISTORY='1'
$env:OLLAMA_DEBUG='0'
$env:OLLAMA_NUM_PARALLEL='1'
$env:OLLAMA_MAX_LOADED_MODELS='1'
$env:OLLAMA_CONTEXT_LENGTH='4096'
$env:OLLAMA_KEEP_ALIVE='0'
$env:OLLAMA_VULKAN='0'
$env:HTTP_PROXY='';$env:HTTPS_PROXY='';$env:ALL_PROXY='';$env:NO_PROXY='127.0.0.1,localhost'
Set-Location $PSScriptRoot
$ErrorActionPreference='Continue' # Native stderr is logging, not a terminating PowerShell error.
& (Join-Path $PSScriptRoot 'ollama-v0.40.11\ollama.exe') serve *>> (Join-Path $PSScriptRoot 'runtime.log')
exit $LASTEXITCODE
