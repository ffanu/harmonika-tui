#!/bin/sh
# Publish an engine release: manifest (update.json), signature and binaries.
#
#   engine/scripts/release-engine.sh 1.0.1                 # pakai binary di engine/bin
#   engine/scripts/release-engine.sh 1.0.1 --build         # build dulu (host target)
#   VPS=user@host engine/scripts/release-engine.sh 1.0.1 --build --upload
#
# Manifest shape consumed by `harmonika upgrade`:
#   { version, notes[], binaries{ <os-arch>: { url, sha256 } } }
set -eu

VERSION=${1:?pakai: release-engine.sh <version> [--build] [--upload]}
shift
BUILD=0
UPLOAD=0
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --upload) UPLOAD=1 ;;
    *) echo "argumen tidak dikenal: $arg" >&2; exit 2 ;;
  esac
done

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SELF_DIR/../.." && pwd)
BIN_DIR=${BIN_DIR:-$ROOT/engine/bin}
ASSETS_URL=${ASSETS_URL:-https://chat.harmonika.id/assets/harmonika-terminal/engine}
VPS=${VPS:-vps}
REMOTE_DIR=${REMOTE_DIR:-/srv/harmonika-web-chat/current/assets/harmonika-terminal/engine}

if [ "$BUILD" = "1" ]; then
  "$SELF_DIR/build-engine.sh" "${TARGETS:-darwin-arm64}"
fi

python3 - "$VERSION" "$BIN_DIR" "$ASSETS_URL" "$ROOT" <<'PY'
import hashlib, json, os, sys
from pathlib import Path

version, bin_dir, assets_url, root = sys.argv[1:5]
bin_dir = Path(bin_dir)
binaries = {}
for path in sorted(bin_dir.glob('harmonika*')):
    name = path.name
    if name.startswith('harmonika-'):
        key = name[len('harmonika-'):]
        if key.endswith('.exe'):
            key = key[:-4]
    else:
        key = 'darwin-arm64'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    binaries[key] = {'url': f'{assets_url}/{name}', 'sha256': digest, 'size': path.stat().st_size,
                     'file': name}
manifest = {
    'product': 'Harmonika AI',
    'vendor': 'Harmonika ID',
    'channel': 'stable',
    'version': version,
    'notes': [
        'Brand layer tunggal (@opencode/util/brand) dengan shim env HARMONIKA_*/OPENCODE_*.',
        'Upgrade engine hanya dari manifest Harmonika ID; jalur npm/brew/winget dimatikan.',
        'Web UI tertanam di binary (serve/pair menyajikan UI, bukan 404).',
        'LSP, formatter, agents, snapshots aktif lewat konfigurasi.',
    ],
    'binaries': {k: {'url': v['url'], 'sha256': v['sha256']} for k, v in binaries.items()},
}
out = Path(root) / 'engine' / 'update.json'
out.write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'manifest': str(out), 'platforms': sorted(binaries), 'version': version}))
PY

if [ -f "$ROOT/../harmonika-terminal/signing-private.pem" ]; then
  : # signer lives in the terminal package; handled below when present
fi
SIGNER=${SIGNER:-$HOME/.config/harmonika-terminal/signing-private.pem}
if [ -f "$SIGNER" ] && command -v node >/dev/null 2>&1; then
  node -e '
const fs=require("fs"),c=require("crypto");
const p=fs.readFileSync(process.argv[1]);
const sig=c.sign(null,p,fs.readFileSync(process.argv[2]));
fs.writeFileSync(process.argv[1]+".sig", sig.toString("base64")+"\n");
console.log("signed", process.argv[1]+".sig");
' "$ROOT/engine/update.json" "$SIGNER"
else
  printf 'catatan: manifest tidak ditandatangani (signer tidak tersedia)\n'
fi

if [ "$UPLOAD" = "1" ]; then
  printf 'mengunggah ke %s:%s\n' "$VPS" "$REMOTE_DIR"
  ssh -o BatchMode=yes "$VPS" "mkdir -p '$REMOTE_DIR'"
  scp -q "$ROOT/engine/update.json" "$ROOT/engine/update.json.sig" "$VPS:$REMOTE_DIR/" 2>/dev/null || scp -q "$ROOT/engine/update.json" "$VPS:$REMOTE_DIR/"
  for f in "$BIN_DIR"/harmonika*; do
    [ -f "$f" ] || continue
    printf 'upload %s\n' "$(basename "$f")"
    scp -q "$f" "$VPS:$REMOTE_DIR/"
  done
fi

printf 'selesai: %s/engine/update.json\n' "$ROOT"
