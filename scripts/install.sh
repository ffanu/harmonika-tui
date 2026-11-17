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

INTERACTIVE=0
if [ "${HARMONIKA_NONINTERACTIVE:-0}" != 1 ] && [ -t 1 ]; then
  if [ -t 0 ] || ( : </dev/tty ) 2>/dev/null; then
    INTERACTIVE=1
  fi
fi

# curl | sh owns stdin, but a real terminal remains available on /dev/tty.
# Validate privately before installing/entering the agent. Never read a key
# from the downloaded script stream or pass one in process arguments.
if [ "$INTERACTIVE" = 1 ]; then
  if [ -t 0 ]; then
    python3 "$INSTALL_SRC/runtime/license.py" ensure
  else
    python3 "$INSTALL_SRC/runtime/license.py" ensure </dev/tty
  fi
fi

python3 "$INSTALL_SRC/runtime/install.py"

if [ ! -f "${HARMONIKA_GLOBAL_CONFIG:-$HOME/.config/harmonika}/harmonika-api-key" ]; then
  echo "Aktivasi lisensi dapat dilakukan setelah instalasi: harmonika activate"
fi

if [ "$INTERACTIVE" = 1 ] && [ "${HARMONIKA_LAUNCH_AFTER_INSTALL:-1}" != 0 ]; then
  echo "Membuka Harmonika Ai Terminal..."
  if [ -t 0 ]; then
    exec "${HARMONIKA_BIN_DIR:-$HOME/.local/bin}/harmonika"
  else
    exec "${HARMONIKA_BIN_DIR:-$HOME/.local/bin}/harmonika" </dev/tty
  fi
fi

echo ""
if command -v harmonika >/dev/null 2>&1; then
  echo "Instalasi selesai! Jalankan: harmonika"
else
  echo 'Instalasi selesai. Untuk shell saat ini, jalankan:'
  python3 - <<'PYTHON'
import os, pathlib, shlex
p = os.environ.get("HARMONIKA_BIN_DIR") or str(pathlib.Path.home()/".local/bin")
print("export PATH=" + shlex.quote(p) + ':"$PATH"')
PYTHON
  echo '  harmonika'
fi
