# Harmonika context transport guard

Portable source of the Mac operator context guard. The server plugin projects
outbound Harmonika requests only; the native session database and executor
receipts remain unchanged. Historical omissions are marked and never count as
new execution evidence. Current instructions, user requests and tool arguments
are retained. The operator profile distinguishes 872000 context tokens from a
2800000-character transport target; these settings are not a claim of a full
872000-token inference test or a guarantee that an oversized request is accepted.

Register the directory as a native **server** plugin in `opencode.json`:

```json
{"plugins": [{"package": "/installed/tools/plugins/context-capacity"}]}
```

Do not register this package as a TUI plugin. No API key or saved account belongs
in the package. It has no external JavaScript dependencies and supports Node 18+
for unit checks; production hooks run in the native engine runtime. It copies no
Mac projection-state file. New mechanics are written privately to
`XDG_STATE_HOME/harmonika-terminal/context-capacity/projection-state.json`, falling
back to `~/.local/state`. Unknown role names and message/tool content are excluded
from this file. Signed package files remain immutable.
