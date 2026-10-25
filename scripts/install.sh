#!/bin/sh
# Harmonika TUI Cross-Platform Installer (macOS & Linux)
set -eu

echo "=== Harmonika TUI Installer (macOS / Linux) ==="
command -v python3 >/dev/null 2>&1 || { echo "Error: python3 diperlukan."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "Error: node (Node.js) diperlukan."; exit 1; }
command -v git >/dev/null 2>&1 || { echo "Error: git diperlukan."; exit 1; }

INSTALL_SRC="${HARMONIKA_SOURCE_DIR:-$HOME/.local/share/harmonika-tui-src}"
if [ -d "$INSTALL_SRC/.git" ]; then
  git -C "$INSTALL_SRC" pull --ff-only
else
  rm -rf "$INSTALL_SRC"
  git clone https://github.com/ffanu/harmonika-tui.git "$INSTALL_SRC"
fi

python3 "$INSTALL_SRC/runtime/install.py"

if [ ! -f "$HOME/.config/harmonika/harmonika-api-key" ]; then
  echo ""
  echo "Silakan masukkan API Key Harmonika Anda (dari Bot Telegram @hmsapi_bot):"
  python3 "$INSTALL_SRC/runtime/license.py" activate
fi

echo ""
echo "Instalasi selesai! Pastikan ~/.local/bin ada di PATH Anda, lalu jalankan:"
echo "  harmonika"
