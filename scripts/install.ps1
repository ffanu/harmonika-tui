# Harmonika TUI Installer for Windows PowerShell (Auto-Detect Paths & Antivirus Safe)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== Harmonika TUI Installer (Windows x64) ===" -ForegroundColor Cyan

# 1. Auto-discover Python, Node.js, and Git on Windows even if PATH was reset
$ExtraPaths = @(
    "$HOME\.local\bin",
    "$env:ProgramFiles\nodejs",
    "${env:ProgramFiles(x86)}\nodejs",
    "$env:LOCALAPPDATA\Programs\nodejs",
    "$env:APPDATA\npm",
    "$env:ProgramFiles\Git\cmd",
    "$env:ProgramFiles\Git\bin",
    "${env:ProgramFiles(x86)}\Git\cmd",
    "$env:LOCALAPPDATA\Programs\Git\cmd"
)
if (Test-Path "$env:LOCALAPPDATA\Programs\Python") {
    Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $ExtraPaths += $_.FullName
        $ExtraPaths += Join-Path $_.FullName "Scripts"
    }
}
if (Test-Path "C:\Python*") {
    Get-ChildItem -Path "C:\Python*" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $ExtraPaths += $_.FullName
        $ExtraPaths += Join-Path $_.FullName "Scripts"
    }
}

foreach ($p in $ExtraPaths) {
    if ($p -and (Test-Path $p) -and ($env:Path -notlike "*$p*")) {
        $env:Path = "$p;$env:Path"
    }
}

$PyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $PyCmd) { throw "Python 3 tidak ditemukan. Pastikan Python 3 sudah terinstall." }
$NodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $NodeCmd) { throw "Node.js tidak ditemukan. Pastikan Node.js sudah terinstall." }
$GitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitCmd) { throw "Git tidak ditemukan. Pastikan Git sudah terinstall." }

$PyDir = Split-Path -Parent $PyCmd.Source
$NodeDir = Split-Path -Parent $NodeCmd.Source
$GitDir = Split-Path -Parent $GitCmd.Source
$BinDir = Join-Path $HOME ".local\bin"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

# 2. Add exclusions to Windows Defender if running as Administrator so Antivirus never blocks harmonika.exe
$HarmonikaConfig = Join-Path $HOME ".config\harmonika"
$HarmonikaTerm = Join-Path $HOME ".config\harmonika-terminal"
$InstallSrc = Join-Path $HOME ".local\share\harmonika-tui-src"
try {
    Add-MpPreference -ExclusionPath $HarmonikaConfig, $HarmonikaTerm, $InstallSrc, $BinDir -ErrorAction SilentlyContinue
} catch {}

# 3. Clone or update source repository
if (Test-Path (Join-Path $InstallSrc ".git")) {
    git -C $InstallSrc fetch origin main
    git -C $InstallSrc reset --hard origin/main
} else {
    if (Test-Path $InstallSrc) { Remove-Item -Recurse -Force $InstallSrc }
    git clone https://github.com/ffanu/harmonika-tui.git $InstallSrc
}

# 4. Run Python installer with pinned Node path
$env:HARMONIKA_NODE = $NodeCmd.Source
& $PyCmd.Source (Join-Path $InstallSrc "runtime\install.py")

# 5. Unblock downloaded files so Windows SmartScreen / Defender does not block execution
Get-ChildItem -Path $HarmonikaTerm -Recurse -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

# 6. Persist Python, Node, Git, and Harmonika bin directory permanently in Windows User PATH & PowerShell $PROFILE
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $UserPath) { $UserPath = "" }
foreach ($dir in @($BinDir, $PyDir, $NodeDir, $GitDir)) {
    if ($dir -and (Test-Path $dir) -and ($UserPath -notlike "*$dir*")) {
        $UserPath = if ($UserPath) { "$dir;$UserPath" } else { $dir }
    }
}
[Environment]::SetEnvironmentVariable("Path", $UserPath, "User")

# Also write to PowerShell $PROFILE so every new PowerShell window always has python, node, git, and harmonika
$ProfilePaths = @($PROFILE)
$WinPSProfile = Join-Path $HOME "Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1"
$PSCoreProfile = Join-Path $HOME "Documents\PowerShell\Microsoft.PowerShell_profile.ps1"
foreach ($prof in (@($ProfilePaths + $WinPSProfile + $PSCoreProfile) | Select-Object -Unique)) {
    if ($prof) {
        try {
            $profDir = Split-Path -Parent $prof
            if (-not (Test-Path $profDir)) { New-Item -ItemType Directory -Force -Path $profDir | Out-Null }
            $marker = "# >>> Harmonika TUI >>>"
            $existing = if (Test-Path $prof) { Get-Content $prof -Raw -ErrorAction SilentlyContinue } else { "" }
            if ($existing -notlike "*$marker*") {
                $block = @"

# >>> Harmonika TUI >>>
`$env:HARMONIKA_NODE = "$($NodeCmd.Source)"
foreach (`$d in @("$BinDir", "$PyDir", "$NodeDir", "$GitDir")) {
    if (`$d -and (Test-Path `$d) -and (`$env:Path -notlike "*`$d*")) { `$env:Path = "`$d;`$env:Path" }
}
function harmonika { & "$($PyCmd.Source)" -I "$HarmonikaTerm\launcher.py" @args }
# <<< Harmonika TUI <<<
"@
                Add-Content -Path $prof -Value $block -Encoding UTF8
            }
        } catch {}
    }
}

# Define harmonika function in current PowerShell session immediately
Set-Item -Path Function:\global:harmonika -Value ([scriptblock]::Create("& `"$($PyCmd.Source)`" -I `"$HarmonikaTerm\launcher.py`" @args"))

# 7. Activate license if not yet configured
$KeyFile = Join-Path $HOME ".config\harmonika\harmonika-api-key"
if (-not (Test-Path $KeyFile)) {
    Write-Host ""
    $InputKey = Read-Host "Masukkan / Paste API Key Lisensi Harmonika Anda (hai_live_...)"
    if ($InputKey) {
        & $PyCmd.Source (Join-Path $InstallSrc "runtime\license.py") activate $InputKey.Trim()
    } else {
        & $PyCmd.Source (Join-Path $InstallSrc "runtime\license.py") activate
    }
}

Write-Host "`nInstalasi & konfigurasi PATH permanen selesai! Ketik perintah berikut untuk membuka Harmonika TUI:" -ForegroundColor Green
Write-Host "  harmonika" -ForegroundColor Yellow


