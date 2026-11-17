#!/bin/sh
# Harmonika TUI Cross-Platform Installer (macOS & Linux)
set -eu

echo "=== Harmonika TUI Installer (macOS / Linux) ==="
command -v python3 >/dev/null 2>&1 || { echo "Error: python3 diperlukan."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "Error: node (Node.js) diperlukan."; exit 1; }
command -v git >/dev/null 2>&1 || { echo "Error: git diperlukan."; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || { echo "Error: Python 3.9 atau lebih baru diperlukan."; exit 1; }
node -e 'if(Number(process.versions.node.split(".")[0])<18)process.exit(1)' || { echo "Error: Node.js 18 atau lebih baru diperlukan."; exit 1; }

INSTALL_SRC="${HARMONIKA_SOURCE_DIR:-$HOME/.local/share/harmonika-tui-src}"
if [ -d "$INSTALL_SRC/.git" ]; then
  git -C "$INSTALL_SRC" pull --ff-only
else
  if [ -e "$INSTALL_SRC" ]; then
    echo "Error: folder sumber sudah ada dan bukan repository Git: $INSTALL_SRC" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$INSTALL_SRC")"
  git clone https://github.com/ffanu/harmonika-tui.git "$INSTALL_SRC"
fi

python3 "$INSTALL_SRC/runtime/install.py"

if [ "${HARMONIKA_NONINTERACTIVE:-0}" != 1 ] && [ -t 0 ] && [ ! -f "${HARMONIKA_GLOBAL_CONFIG:-$HOME/.config/harmonika}/harmonika-api-key" ]; then
  echo ""
  echo "Silakan masukkan API Key Harmonika Anda (dari Bot Telegram @hmsapi_bot):"
  python3 "$INSTALL_SRC/runtime/license.py" activate
fi
if [ ! -f "${HARMONIKA_GLOBAL_CONFIG:-$HOME/.config/harmonika}/harmonika-api-key" ]; then
  echo "Aktivasi lisensi dapat dilakukan setelah instalasi: harmonika activate"
fi

echo ""
echo "Instalasi selesai! Pastikan ~/.local/bin ada di PATH Anda, lalu jalankan:"
echo "  harmonika"
