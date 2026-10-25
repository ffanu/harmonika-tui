# Harmonika TUI Installer for Windows PowerShell
$ErrorActionPreference = "Stop"

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
    git -C $InstallSrc pull --ff-only
} else {
    if (Test-Path $InstallSrc) { Remove-Item -Recurse -Force $InstallSrc }
    git clone https://github.com/ffanu/harmonika-tui.git $InstallSrc
}

python (Join-Path $InstallSrc "runtime\install.py")

$KeyFile = Join-Path $HOME ".config\harmonika\harmonika-api-key"
if (-not (Test-Path $KeyFile)) {
    Write-Host "`nMasukkan API Key Harmonika Anda (dari Bot Telegram @hmsapi_bot):" -ForegroundColor Yellow
    python (Join-Path $InstallSrc "runtime\license.py") activate
}

$BinDir = Join-Path $HOME ".local\bin"
Write-Host "`nInstalasi selesai! Tambahkan $BinDir ke PATH Windows Anda, lalu jalankan:" -ForegroundColor Green
Write-Host "  harmonika"
