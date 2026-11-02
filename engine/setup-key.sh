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

umask 077
printf '%s\n' "$KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE"
printf 'Key tersimpan di %s (0600).\n' "$KEY_FILE"
