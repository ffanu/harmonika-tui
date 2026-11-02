#!/bin/sh
# Build Harmonika engine binaries from the rebranded fork.
#
#   engine/scripts/build-engine.sh                      # darwin-arm64 (host)
#   engine/scripts/build-engine.sh darwin-arm64 linux-x64 windows-x64
#   SRC=/path/to/harmonika-tui-src engine/scripts/build-engine.sh
#
# Requires bun. Web UI assets are embedded (never pass --skip-web-ui).
set -eu

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SELF_DIR/../.." && pwd)
SRC=${SRC:-$HOME/Projects/harmonika-tui-src}
BUN=${BUN:-$(command -v bun || echo "$HOME/.bun/bin/bun")}
OUT=${OUT:-$ROOT/engine/bin}

[ -x "$BUN" ] || { echo "bun tidak ditemukan" >&2; exit 1; }
[ -d "$SRC/packages/cli" ] || { echo "source tidak ditemukan: $SRC" >&2; exit 1; }

if [ $# -gt 0 ]; then TARGETS="$@"; else TARGETS="darwin-arm64"; fi
mkdir -p "$OUT"

cd "$SRC/packages/cli"
for target in $TARGETS; do
  printf '== build %s\n' "$target"
  rm -rf dist
  OPENCODE_TARGET="$target" "$BUN" run script/build.ts
  case "$target" in
    windows-*) name=harmonika-${target}.exe; artifact=opencode.exe ;;
    *) name=harmonika-${target}; artifact=opencode ;;
  esac
  cp "dist/cli-$target/bin/$artifact" "$OUT/$name"
  chmod +x "$OUT/$name"
  shasum -a 256 "$OUT/$name" | awk '{print "   sha256 " $1}'
done

printf 'selesai: %s\n' "$OUT"
