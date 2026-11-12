param(
    [ValidateSet('Gateway','Comfy')][string]$Component,
    [string]$Root = (Join-Path $env:ProgramData 'Harmonika\RTXKeepAlive')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-RTXListener {
    param([int]$Port, [int]$TimeoutMilliseconds = 700)
    $client = New-Object System.Net.Sockets.TcpClient
    $operation = $null
    try {
        $operation = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $operation.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) { return $false }
        $client.EndConnect($operation)
        return $client.Connected
    } catch { return $false }
    finally {
        if ($null -ne $operation) { $operation.AsyncWaitHandle.Close() }
        $client.Close()
    }
}

function Get-RTXRecoveryDecision {
    param([bool]$Maintenance, [bool]$Enabled, [string]$TaskState)
    if ($Maintenance) { return 'maintenance' }
    if (-not $Enabled) { return 'enable_and_start' }
    if ($TaskState -in @('Running','Queued')) { return 'observe' }
    return 'start'
}

function Write-RTXMechanics {
    param([string]$StateRoot, [string]$Name, [hashtable]$Fields)
    if ($Name -notmatch '^[A-Za-z0-9_-]{1,48}$') { throw 'invalid_component' }
    $state = [ordered]@{schema_version=1; component=$Name; timestamp=(Get-Date).ToUniversalTime().ToString('o')}
    # Exact whitelist. Never persist stdout, exception messages, paths, prompts or keys.
    foreach ($key in @('status','pid','launches','exit_code','backoff_seconds','latency_ms')) {
        if ($Fields.ContainsKey($key)) { $state[$key] = $Fields[$key] }
    }
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    $target = Join-Path $StateRoot ($Name + '.json')
    $temp = Join-Path $StateRoot ('.state-' + [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllText($temp, ($state | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temp -Destination $target -Force
    } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force } }
}

function Invoke-RTXSupervisor {
    param(
        [Parameter(Mandatory=$true)]$Configuration,
        [Parameter(Mandatory=$true)][string]$StateRoot,
        [Parameter(Mandatory=$true)][string]$MaintenanceFile,
        [int]$MaximumLaunches = 0,
        [int]$InitialBackoffSeconds = 5,
        [int]$ObserveSeconds = 10
    )
    $name = [string]$Configuration.name
    if ($name -notmatch '^[A-Za-z0-9_-]{1,48}$' -or $Configuration.port -lt 1 -or $Configuration.port -gt 65535) { throw 'invalid_component' }
    if (-not (Test-Path -LiteralPath $Configuration.executable -PathType Leaf)) { throw 'runtime_missing' }
    if (-not (Test-Path -LiteralPath $Configuration.directory -PathType Container)) { throw 'directory_missing' }
    if ($InitialBackoffSeconds -lt 1 -or $ObserveSeconds -lt 1 -or $MaximumLaunches -lt 0) { throw 'invalid_runtime_settings' }
    $mutex = New-Object System.Threading.Mutex($false, ('Global\HarmonikaRTX-' + $name))
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { return }
        $launches = 0; $failures = 0
        while ($MaximumLaunches -eq 0 -or $launches -lt $MaximumLaunches) {
            if (Test-Path -LiteralPath $MaintenanceFile) {
                Write-RTXMechanics $StateRoot $name @{status='maintenance';launches=$launches}
                return
            }
            # A pre-existing listener belongs to its original operator. Never adopt,
            # interrupt or kill it, and never load a duplicate GPU model alongside it.
            if (Test-RTXListener -Port $Configuration.port) {
                Write-RTXMechanics $StateRoot $name @{status='existing_listener';launches=$launches}
                Start-Sleep -Seconds $ObserveSeconds
                continue
            }
            $process = $null; $started = [Diagnostics.Stopwatch]::StartNew()
            try {
                $info = New-Object Diagnostics.ProcessStartInfo
                $info.FileName = [string]$Configuration.executable
                # The app replaces both Python/Win32 stdio handles with NUL
                # before importing runtime code. An orphan's progress output
                # must not fail if its old supervisor/pipe disappears.
                $info.Arguments = '-u "' + (Join-Path $PSScriptRoot 'ChildRuntime.py') + '" ' + [string]$Configuration.arguments
                $info.WorkingDirectory = [string]$Configuration.directory
                $info.UseShellExecute = $false
                $info.CreateNoWindow = $true
                $info.RedirectStandardOutput = $true
                $info.RedirectStandardError = $true
                foreach ($property in $Configuration.environment.PSObject.Properties) {
                    $info.EnvironmentVariables[$property.Name] = [string]$property.Value
                }
                $process = New-Object Diagnostics.Process
                $process.StartInfo = $info
                if (-not $process.Start()) { throw 'process_start_failed' }
                $launches++
                # Drain and discard both streams. No raw model/customer output logs.
                $process.BeginOutputReadLine(); $process.BeginErrorReadLine()
                Write-RTXMechanics $StateRoot $name @{status='process_started';pid=$process.Id;launches=$launches}
                $process.WaitForExit()
                $exitCode = $process.ExitCode
                $started.Stop()
                Write-RTXMechanics $StateRoot $name @{status='process_exited';launches=$launches;exit_code=$exitCode;latency_ms=$started.ElapsedMilliseconds}
                if ($started.Elapsed.TotalSeconds -ge 120) { $failures = 0 }
            } catch {
                Write-RTXMechanics $StateRoot $name @{status='start_failed';launches=$launches}
            } finally { if ($null -ne $process) { $process.Dispose() } }
            if ($MaximumLaunches -gt 0 -and $launches -ge $MaximumLaunches) { return }
            $failures++
            $delay = [int][Math]::Min(60, $InitialBackoffSeconds * [Math]::Pow(2, [Math]::Min(4, $failures - 1)))
            Write-RTXMechanics $StateRoot $name @{status='retry_wait';launches=$launches;backoff_seconds=$delay}
            Start-Sleep -Seconds $delay
        }
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        if (-not $Component) { throw 'component_required' }
        $config = Get-Content -LiteralPath (Join-Path $Root 'components.json') -Raw | ConvertFrom-Json
        $selected = @($config.components | Where-Object {$_.name -eq $Component})
        if ($selected.Count -ne 1) { throw 'invalid_component' }
        Invoke-RTXSupervisor -Configuration $selected[0] -StateRoot (Join-Path $Root 'state') -MaintenanceFile (Join-Path $Root 'maintenance.enabled')
        exit 0
    } catch { exit 1 }
}
