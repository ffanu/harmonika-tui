#!/bin/sh
# harmonika-setup — ganti atau set Harmonika API key tanpa instalasi ulang.
set -e
KEY_FILE="$HOME/.config/harmonika/harmonika-api-key"
mkdir -p "$HOME/.config/harmonika"
chmod 700 "$HOME/.config/harmonika" 2>/dev/null || true

if [ "$1" = "--show" ]; then
  if [ -r "$KEY_FILE" ]; then
    KEY=$(tr -d '\r\n' < "$KEY_FILE")
    printf 'Tersimpan: %s...%s\n' "$(printf '%s' "$KEY" | cut -c1-12)" "$(printf '%s' "$KEY" | rev | cut -c1-4 | rev)"
  else
    printf 'Belum ada key tersimpan.\n'
  fi
  exit 0
fi

if [ -n "$1" ]; then
  KEY=$1
else
  printf '%s' 'Harmonika AI API key baru: '
  stty -echo 2>/dev/null || true
  read -r KEY
  stty echo 2>/dev/null || true
  printf '\n'
fi

if [ -z "$KEY" ]; then
  printf 'Key kosong, tidak ada perubahan.\n' >&2
  exit 2
fi

LICENSE_PY=""
for candidate in "$HOME/Projects/harmonika-tui/runtime/license.py" \
                 "$HOME/.local/share/harmonika-tui-src/runtime/license.py" \
                 "$HOME/.config/harmonika-terminal/current/tools/license.py"; do
  if [ -f "$candidate" ]; then LICENSE_PY="$candidate"; break; fi
done

if [ -n "$LICENSE_PY" ] && command -v python3 >/dev/null 2>&1; then
  if python3 "$LICENSE_PY" activate "$KEY" >/dev/null 2>&1; then
    printf 'Key terverifikasi server dan terkunci untuk perangkat ini (1 key = 1 device).\n'
    exit 0
  fi
  printf 'Peringatan: verifikasi server gagal; key tetap disimpan untuk percobaan berikutnya.\n' >&2
fi

umask 077
printf '%s\n' "$KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE"
printf 'Key tersimpan di %s (0600).\n' "$KEY_FILE"
