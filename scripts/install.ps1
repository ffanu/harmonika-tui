# Harmonika TUI Installer for Windows PowerShell
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== Harmonika TUI Installer (Windows x64) ===" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3 diperlukan. Silakan install Python 3 terlebih dahulu."
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js diperlukan. Silakan install Node.js terlebih dahulu."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git diperlukan. Silakan install Git terlebih dahulu."
}

$InstallSrc = Join-Path $HOME ".local\share\harmonika-tui-src"
if (Test-Path (Join-Path $InstallSrc ".git")) {
    git -C $InstallSrc fetch origin main
    git -C $InstallSrc reset --hard origin/main
} else {
    if (Test-Path $InstallSrc) { Remove-Item -Recurse -Force $InstallSrc }
    git clone https://github.com/ffanu/harmonika-tui.git $InstallSrc
}

python (Join-Path $InstallSrc "runtime\install.py")

$BinDir = Join-Path $HOME ".local\bin"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$BinDir;$UserPath", "User")
}
if ($env:Path -notlike "*$BinDir*") {
    $env:Path = "$BinDir;$env:Path"
}

$KeyFile = Join-Path $HOME ".config\harmonika\harmonika-api-key"
if (-not (Test-Path $KeyFile)) {
    Write-Host ""
    $InputKey = Read-Host "Masukkan / Paste API Key Lisensi Harmonika Anda (hai_live_...)"
    if ($InputKey) {
        python (Join-Path $InstallSrc "runtime\license.py") activate $InputKey.Trim()
    } else {
        python (Join-Path $InstallSrc "runtime\license.py") activate
    }
}

Write-Host "`nInstalasi selesai! Ketik perintah berikut untuk membuka Harmonika TUI:" -ForegroundColor Green
Write-Host "  harmonika" -ForegroundColor Yellow

