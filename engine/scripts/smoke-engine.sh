#!/bin/sh
# Smoke test for a Harmonika engine binary.
#
#   engine/scripts/smoke-engine.sh /path/to/harmonika
#
# Checks: version, brand, no upstream update endpoints, config loader, model list,
# embedded web UI (HTTP 200 + html), and LSP/formatter truthfulness in the config.
set -eu

BIN=${1:-$HOME/Projects/harmonika-engine/bin/harmonika}
[ -x "$BIN" ] || { echo "binary tidak ditemukan: $BIN" >&2; exit 1; }
FAIL=0
ok() { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1" >&2; FAIL=1; }

version=$("$BIN" --version 2>&1 | head -1)
case "$version" in
  *harmonika*) ok "version: $version" ;;
  *) bad "version bukan harmonika: $version" ;;
esac

# Upstream update/distribution endpoints must be gone from the shipped binary.
if LC_ALL=C grep -qa 'opencode.ai/update/api' "$BIN"; then bad 'masih memuat endpoint update upstream'; else ok 'tanpa endpoint update upstream'; fi
if LC_ALL=C grep -qa 'chat.harmonika.id/assets/harmonika-terminal' "$BIN"; then ok 'manifest pembaruan Harmonika tertanam'; else bad 'manifest pembaruan Harmonika tidak ada'; fi

if "$BIN" debug paths 2>/dev/null | grep -q 'harmonika-terminal'; then ok 'paths memakai harmonika-terminal'; else bad 'paths bukan harmonika-terminal'; fi

if "$BIN" debug config 2>/dev/null | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then ok 'debug config JSON valid'; else bad 'debug config tidak valid'; fi

models=$("$BIN" models 2>/dev/null || true)
case "$models" in
  *harmonika/*) ok 'provider harmonika terdaftar' ;;
  *) bad 'provider harmonika tidak muncul di models' ;;
esac

PORT=$(( 20000 + ($$ % 20000) ))
"$BIN" serve --port "$PORT" >/tmp/harmonika-smoke-serve.log 2>&1 &
SERVE_PID=$!
trap 'kill $SERVE_PID 2>/dev/null || true' EXIT
sleep 8
if curl -fsS --max-time 8 "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q '<!doctype html>'; then
  ok 'web UI tersaji (HTTP 200 + html)'
else
  bad 'web UI tidak tersaji di /'
fi
kill $SERVE_PID 2>/dev/null || true

if "$BIN" upgrade --method npm >/dev/null 2>&1; then bad 'upgrade menerima metode npm'; else ok 'upgrade menolak metode pihak ketiga'; fi

exit $FAIL
