#!/bin/sh
# Prove the published patch series still reproduces the rebranded tree.
#
#   engine/scripts/verify-patches.sh [base-commit]
#
# Creates a temporary worktree at the upstream base, applies every patch in
# engine/patches in order, then compares the resulting tree with harmonika/main.
# Exit 0 means the series is complete and conflict-free.
set -eu

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SELF_DIR/../.." && pwd)
SRC=${SRC:-$HOME/Projects/harmonika-tui-src}
BASE=${1:-$(git -C "$SRC" rev-parse harmonika/main~2)}
BRANCH=${BRANCH:-harmonika/main}
PATCHES="$ROOT/engine/patches"
WORK=$(mktemp -d /tmp/harmonika-verify.XXXXXX)

trap 'git -C "$SRC" worktree remove --force "$WORK" >/dev/null 2>&1 || rm -rf "$WORK"' EXIT

git -C "$SRC" worktree add --detach "$WORK" "$BASE" >/dev/null
for patch in "$PATCHES"/*.patch; do
  [ -f "$patch" ] || continue
  printf 'apply %s\n' "$(basename "$patch")"
  git -C "$WORK" apply --index "$patch"
done

rebuilt=$(git -C "$WORK" write-tree)
expected=$(git -C "$SRC" rev-parse "$BRANCH^{tree}")
if [ "$rebuilt" = "$expected" ]; then
  printf 'ok   patch series mereproduksi %s (%s)\n' "$BRANCH" "$(printf %s "$rebuilt" | cut -c1-12)"
else
  printf 'FAIL tree berbeda: %s != %s\n' "$rebuilt" "$expected" >&2
  git -C "$WORK" status --short | head -20 >&2
  exit 1
fi
