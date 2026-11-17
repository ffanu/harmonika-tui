# Harmonika Terminal installer — Windows x64 / PowerShell 5.1+
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Host '=== Harmonika Terminal Installer (Windows x64) ===' -ForegroundColor Cyan

$ExtraPaths = @("$HOME\.local\bin", "$env:ProgramFiles\nodejs", "${env:ProgramFiles(x86)}\nodejs", "$env:LOCALAPPDATA\Programs\nodejs", "$env:APPDATA\npm", "$env:ProgramFiles\Git\cmd", "$env:LOCALAPPDATA\Programs\Git\cmd")
if (Test-Path "$env:LOCALAPPDATA\Programs\Python") {
    Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Directory | ForEach-Object { $ExtraPaths += $_.FullName }
}
foreach ($dir in $ExtraPaths) {
    if ($dir -and (Test-Path $dir) -and (($env:Path -split ';') -notcontains $dir)) { $env:Path = "$dir;$env:Path" }
}
$PyCmd = $null
$PyArgs = @()
# PowerShell 5 can turn a native stderr diagnostic into a terminating error
# under Stop. A broken Python candidate must not prevent trying python3/py.
function Test-HarmonikaPython {
    param([string]$Executable, [string[]]$Prefix)
    $ErrorActionPreference = 'Continue'
    try {
        & $Executable @Prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) and sys.maxsize > 2**32 else 1)' 1>$null 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}
foreach ($name in @('python', 'python3', 'py')) {
    $candidate = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $candidate) { continue }
    $prefix = @()
    if ($name -eq 'py') { $prefix = @('-3') }
    if (Test-HarmonikaPython -Executable $candidate.Source -Prefix $prefix) { $PyCmd = $candidate; $PyArgs = $prefix; break }
}
if (-not $PyCmd) { throw 'Python 64-bit versi 3.9 atau lebih baru diperlukan. Instal Python 64-bit lalu jalankan ulang PowerShell.' }
$NodeCmd = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $NodeCmd) { throw 'Node.js 18 atau lebih baru diperlukan.' }
& $NodeCmd.Source -e 'if(parseInt(process.versions.node,10)<18)process.exit(1)'
if ($LASTEXITCODE -ne 0) { throw 'Node.js 18 atau lebih baru diperlukan.' }
$GitCmd = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $GitCmd) { throw 'Git diperlukan.' }
$env:HARMONIKA_NODE = $NodeCmd.Source
$InstallSrc = if ($env:HARMONIKA_SOURCE_DIR) { $env:HARMONIKA_SOURCE_DIR } else { Join-Path $HOME '.local\share\harmonika-tui-src' }
$BinDir = if ($env:HARMONIKA_BIN_DIR) { $env:HARMONIKA_BIN_DIR } else { Join-Path $HOME '.local\bin' }
$ConfigDir = if ($env:HARMONIKA_GLOBAL_CONFIG) { $env:HARMONIKA_GLOBAL_CONFIG } else { Join-Path $HOME '.config\harmonika' }
if (Test-Path (Join-Path $InstallSrc '.git')) {
    & $GitCmd.Source -C $InstallSrc pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw 'Pembaruan source gagal. Perubahan lokal tetap dipertahankan.' }
} else {
    if (Test-Path $InstallSrc) { throw "Folder sumber sudah ada dan bukan repository Git: $InstallSrc" }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InstallSrc) | Out-Null
    & $GitCmd.Source clone https://github.com/ffanu/harmonika-tui.git $InstallSrc
    if ($LASTEXITCODE -ne 0) { throw 'Clone source gagal.' }
}
$Interactive = $env:HARMONIKA_NONINTERACTIVE -ne '1' -and -not [Console]::IsInputRedirected
if ($Interactive) {
    Write-Host 'Tempel lisensi dengan Ctrl+V atau Shift+Insert, lalu Enter. Key akan terlihat saat dimasukkan.'
    & $PyCmd.Source @PyArgs (Join-Path $InstallSrc 'runtime\license.py') ensure
    if ($LASTEXITCODE -ne 0) { throw 'Aktivasi lisensi belum selesai.' }
}
& $PyCmd.Source @PyArgs (Join-Path $InstallSrc 'runtime\install.py')
if ($LASTEXITCODE -ne 0) { throw 'Instalasi Harmonika gagal; lihat diagnostik di atas.' }
# Per-user PATH only. Do not alter Defender settings, System32 or shell profiles.
$env:Path = "$BinDir;$env:Path"
if (-not $env:HARMONIKA_BIN_DIR) {
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($UserPath -split ';') -notcontains $BinDir) {
        [Environment]::SetEnvironmentVariable('Path', "$BinDir;$UserPath", 'User')
    }
}
$KeyFile = Join-Path $ConfigDir 'harmonika-api-key'
if ($Interactive -and $env:HARMONIKA_LAUNCH_AFTER_INSTALL -ne '0') {
    & (Join-Path $BinDir 'harmonika.cmd')
    if ($LASTEXITCODE -ne 0) { throw 'Terminal belum berhasil dibuka; jalankan harmonika doctor.' }
}
Write-Host 'Instalasi selesai. Jalankan harmonika, atau harmonika activate untuk aktivasi lisensi.' -ForegroundColor Green
