#!/bin/sh
# Harmonika TUI installer: rebranded engine + Harmonika API key.
#
#   sh engine/install.sh                 # interactive: asks for the API key
#   sh engine/install.sh --api-key hai_live_xxx
#   sh engine/install.sh --engine-home /opt/harmonika --no-launcher
#
# It writes the owner-only key file, renders the provider config from
# engine/config/harmonika.json, installs the portable launcher, and prepares the
# auto-save folders. The key is never stored inside the config file.
set -e

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ENGINE_HOME="$SELF_DIR"
API_KEY=""
INSTALL_LAUNCHER=1
WITH_TOOLS=1
MCP_SERVER="$HOME/.config/harmonika-terminal/mcp_server.py"
PYTHON=$(command -v python3 2>/dev/null || true)
[ -n "$PYTHON" ] || PYTHON=/usr/bin/python3

while [ $# -gt 0 ]; do
  case "$1" in
    --api-key) API_KEY="$2"; shift 2 ;;
    --engine-home) ENGINE_HOME=$2; shift 2 ;;
    --mcp-server) MCP_SERVER=$2; shift 2 ;;
    --no-launcher) INSTALL_LAUNCHER=0; shift ;;
    --skip-tools) WITH_TOOLS=0; shift ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

CONFIG_HOME="$HOME/.config/harmonika"
ENGINE_CONFIG_HOME="$HOME/.config/harmonika-terminal"
mkdir -p "$CONFIG_HOME" "$ENGINE_CONFIG_HOME"
chmod 700 "$CONFIG_HOME" "$ENGINE_CONFIG_HOME" 2>/dev/null || true

if [ -z "$API_KEY" ] && [ -r "$CONFIG_HOME/harmonika-api-key" ]; then
  API_KEY=$(tr -d '\r\n' < "$CONFIG_HOME/harmonika-api-key")
fi

if [ -z "$API_KEY" ]; then
  printf '%s' 'Harmonika AI API key (hai_live_... / hai_internal_...): '
  stty -echo 2>/dev/null || true
  read -r API_KEY
  stty echo 2>/dev/null || true
  printf '\n'
fi

if [ -z "$API_KEY" ]; then
  printf 'Harmonika AI: API key kosong, instalasi dibatalkan.\n' >&2
  exit 2
fi

umask 077
VERIFIED=0
LICENSE_PY=""
for candidate in "$SELF_DIR/../runtime/license.py" \
                 "$HOME/.config/harmonika-terminal/current/tools/license.py"; do
  if [ -f "$candidate" ]; then LICENSE_PY="$candidate"; break; fi
done
if [ -n "$LICENSE_PY" ] && command -v python3 >/dev/null 2>&1; then
  if python3 "$LICENSE_PY" activate "$API_KEY" >/dev/null 2>&1; then
    VERIFIED=1
    printf 'API key terverifikasi server dan terkunci untuk perangkat ini (1 key = 1 device).\n'
  else
    printf 'Peringatan: key belum dapat diverifikasi server (offline, kuota, atau sudah terikat perangkat lain).\n' >&2
    printf 'Key tetap disimpan; aktifkan ulang dengan: harmonika-setup\n' >&2
  fi
fi
if [ "$VERIFIED" = "0" ]; then
  printf '%s\n' "$API_KEY" > "$CONFIG_HOME/harmonika-api-key"
fi
chmod 600 "$CONFIG_HOME/harmonika-api-key"
umask 022

TARGET="$ENGINE_CONFIG_HOME/harmonika.json"
if [ -f "$TARGET" ]; then
  cp "$TARGET" "$TARGET.bak.$(date +%Y%m%d%H%M%S)"
fi
# Prefer the MCP server that ships with the terminal package when the default
# path is absent, so a fresh machine does not start with a broken MCP entry.
if [ ! -f "$MCP_SERVER" ]; then
  for candidate in "$SELF_DIR/mcp/mcp_server.py" \
                   "$HOME/.config/harmonika-terminal/current/tools/mcp_server.py" \
                   "$HOME/.config/harmonika-terminal/current/mcp_server.py"; do
    if [ -f "$candidate" ]; then MCP_SERVER="$candidate"; break; fi
  done
fi

if command -v python3 >/dev/null 2>&1; then
  python3 "$SELF_DIR/render-config.py" --home "$HOME" --python "$PYTHON" \
    --mcp-server "$MCP_SERVER" --template "$SELF_DIR/config/harmonika.json" \
    --out "$TARGET"
else
  sed -e "s|@HOME@|$HOME|g" \
      -e "s|@PYTHON@|$PYTHON|g" \
      -e "s|@MCP_SERVER@|$MCP_SERVER|g" \
      "$SELF_DIR/config/harmonika.json" > "$TARGET"
fi
chmod 600 "$TARGET"

printf '%s\n' "Harmonika AI" "Engine resmi untuk Terminal TUI, dikembangkan oleh Harmonika ID (harmonika.id)." \
  "Jika ditanya identitas: jawab Harmonika Ai dari Harmonika ID." \
  "Simpan hasil tanpa folder khusus ke ~/Downloads/harmonika-ai/{gambar,document,zip,pdf}." \
  > "$CONFIG_HOME/HARMONIKA.md"

for folder in gambar document zip pdf; do
  mkdir -p "$HOME/Downloads/harmonika-ai/$folder"
  chmod 700 "$HOME/Downloads/harmonika-ai/$folder"
  touch "$HOME/Downloads/harmonika-ai/$folder/.keep"
done

# ---------------------------------------------------------------- dev toolchain
# LSP servers and the formatter are fetched from public registries. They are only
# installed when the package manager exists; failures are reported, never fatal.
provision_tools() {
  NPM_BIN=$(command -v npm 2>/dev/null || true)
  if [ -n "$NPM_BIN" ]; then
    if ! command -v typescript-language-server >/dev/null 2>&1 || \
       ! command -v vscode-html-language-server >/dev/null 2>&1 || \
       ! command -v prettier >/dev/null 2>&1; then
      printf 'Memasang LSP TypeScript/HTML/CSS/JSON + prettier (npm --prefix %s)...\n' "$HOME/.local"
      "$NPM_BIN" install -g --prefix "$HOME/.local" --silent \
        typescript typescript-language-server vscode-langservers-extracted prettier \
        >/dev/null 2>&1 && printf '  ok\n' || printf '  gagal (lewati)\n' >&2
    fi
  else
    printf 'npm tidak ditemukan: LSP TypeScript/HTML/CSS/JSON dilewati.\n' >&2
  fi

  if ! command -v pyright-langserver >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    VENV="$HOME/.local/share/harmonika-engine/pyright-venv"
    printf 'Memasang pyright di %s...\n' "$VENV"
    python3 -m venv "$VENV" >/dev/null 2>&1 && \
      "$VENV/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 && \
      "$VENV/bin/pip" install --quiet pyright >/dev/null 2>&1 && \
      ln -sf "$VENV/bin/pyright-langserver" "$HOME/.local/bin/pyright-langserver" 2>/dev/null && \
      printf '  ok\n' || printf '  gagal (lewati)\n' >&2
  fi

  if ! command -v gopls >/dev/null 2>&1 && command -v go >/dev/null 2>&1; then
    printf 'Memasang gopls...\n'
    GOBIN="$HOME/.local/bin" go install golang.org/x/tools/gopls@latest >/dev/null 2>&1 && \
      printf '  ok\n' || printf '  gagal (lewati)\n' >&2
  fi
}

if [ "$WITH_TOOLS" = "1" ]; then
  provision_tools || true
fi

if [ "$INSTALL_LAUNCHER" = "1" ]; then
  mkdir -p "$HOME/.local/bin"
  sed -e "s|HARMONIKA_ENGINE_HOME:-\$SELF_DIR|HARMONIKA_ENGINE_HOME:-$ENGINE_HOME|" \
      "$SELF_DIR/harmonika" > "$HOME/.local/bin/harmonika"
  chmod 755 "$HOME/.local/bin/harmonika"
  cp "$SELF_DIR/setup-key.sh" "$HOME/.local/bin/harmonika-setup"
  chmod 755 "$HOME/.local/bin/harmonika-setup"

  case ":$PATH:" in
    *":$HOME/.local/bin:"*) : ;;
    *) printf 'Catatan: tambahkan %s ke PATH agar `harmonika` bisa dipanggil langsung.\n' "$HOME/.local/bin" ;;
  esac
fi

BINARY="$ENGINE_HOME/bin/harmonika"
if [ -x "$BINARY" ]; then
  printf 'Binary: %s\n' "$BINARY"
  ( "$BINARY" --version 2>/dev/null || true ) | head -n 1 || true
else
  printf 'Binary belum ada di %s (lihat engine/README.md bagian Build).\n' "$BINARY"
fi

printf '%s\n' "" "Selesai." \
  "Config   : $TARGET (apiKey memakai {env:HARMONIKA_API_KEY})" \
  "Key file : $CONFIG_HOME/harmonika-api-key (0600)" \
  "Jalankan : harmonika" \
  "Ganti key: harmonika-setup"
