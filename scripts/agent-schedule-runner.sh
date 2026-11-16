#!/bin/bash
# harmonika-agent-schedule — launchd runner untuk scheduled agent tasks.
# Config: ~/.config/harmonika/agent-schedules.json
#   [{"name":"pagi","hour":6,"minute":30,"weekday":[1,2,3,4,5],
#     "task":"Cek health semua service Harmonika dan laporkan ringkas."}]
# Report: ~/M5/harmonika-reports/<name>-<date>.md (auto-offload ke NAS).
set -u
CONF="$HOME/.config/harmonika/agent-schedules.json"
OUT_DIR="$HOME/M5/harmonika-reports"
AGENT="$HOME/Projects/harmonika-tui/tools/agent-v32.js"
KEY_FILE="$HOME/.config/harmonika/harmonika-api-key"
LOG="$HOME/Library/Logs/harmonika-agent-schedule.log"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/harmonika-sched.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT_DIR"
echo "$(date -u +%FT%TZ) run start" >> "$LOG"

[ -f "$CONF" ] || { echo "no config" >> "$LOG"; exit 0; }
[ -f "$AGENT" ] || { echo "no agent" >> "$LOG"; exit 1; }
export HARMONIKA_API_KEY="$(cat "$KEY_FILE" 2>/dev/null)"

NOW=$(date +%s)
python3 - "$CONF" "$NOW" << 'PYEOF' > "$WORK/due.json"
import json, sys
conf, now = sys.argv[1], int(sys.argv[2])
import datetime
local = datetime.datetime.fromtimestamp(now)
try:
    items = json.load(open(conf))
except Exception:
    # Toleran: jam/menit zero-padded ("03") bukan JSON valid -> sanitize.
    raw = open(conf).read()
    import re
    raw = re.sub(r':\s*0+(\d)', r':\1', raw)
    try:
        items = json.load(open(conf)) if raw == open(conf).read() else json.loads(raw)
    except Exception:
        items = []
due = []
for it in items:
    if not isinstance(it, dict) or not it.get("name") or not it.get("task"):
        continue
    h = int(it.get("hour", 0)); m = int(it.get("minute", 0))
    wd = it.get("weekday")
    if isinstance(wd, list) and local.isoweekday() not in wd:
        continue
    slot = local.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
    # window: slot .. slot + 25 menit (launchd StartCalendarInterval fires ~ jam itu)
    if slot <= now < slot + 25 * 60:
        due.append(it)
json.dump(due, sys.stdout)
PYEOF

while IFS= read -r item; do
  NAME=$(printf '%s' "$item" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
  TASK=$(printf '%s' "$item" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task"])')
  [ -z "$NAME" ] && continue
  DATE=$(date +%Y%m%d-%H%M)
  REPORT="$OUT_DIR/${NAME}-${DATE}.md"
  echo "$(date -u +%FT%TZ) running: $NAME" >> "$LOG"
  (
    echo "# Scheduled: $NAME"
    echo "Date: $(date '+%Y-%m-%d %H:%M %Z')"
    echo
    node "$AGENT" --cwd "$WORK" --verification inspection "$TASK" 2>&1
  ) > "$REPORT" 2>&1
  echo "$(date -u +%FT%TZ) done: $NAME -> $REPORT" >> "$LOG"
done < <(python3 -c 'import json; [print(json.dumps(x)) for x in json.load(open("'"$WORK"'/due.json"))]' 2>/dev/null)

echo "$(date -u +%FT%TZ) run end" >> "$LOG"
