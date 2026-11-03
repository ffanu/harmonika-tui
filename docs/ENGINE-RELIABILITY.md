# Engine reliability: `tool_engine_unavailable`

The terminal TUI shows this when the internal planner process exits non-zero. It is not a
message-size limit and not a quota limit; it means the function-calling engine failed.

## Where it comes from

`/opt/harmonika-ai/api/internal-tool-planner.js` spawns one `codex exec` per turn:

```js
child.on('close', code => { if (code !== 0) throw Error('tool_engine_unavailable') })
```

Any non-zero exit becomes that code, and the HTTP layer maps it to `503`. Before the fix the
child's `stderr` was `'ignore'`, so the real reason was never recorded.

## Diagnosis (this host, measured)

| Observation | Value |
| --- | --- |
| Failed decisions | `status=error code=tool_engine_unavailable duration_ms=395..692` — every ~10 s |
| Timeout path | not involved (`tool_engine_timeout` would need 60 s) |
| Synthetic payloads | 95 KiB, 300 KiB and 900 KiB all exit 0 (900 KiB used 168k tokens) |
| 8 parallel planner calls | 8/8 returned 200 |
| Host load average | **376** (375 processes in `D` state) |
| Stuck processes | 294 `fwupd` + 75 `chrome` + 4 `chrome-headless` + 1 `Xvfb`, all `D` |
| Kernel wait channel | `drm_modeset_lock` → `drm_client_modeset_commit` → `amdgpu_driver_lastclose_kms` |
| Crash loop | `harmonika-rtx-vision-tunnel.service` restarted **64,367** times (port 18189 owned by `telco_engine`) |

Conclusion: the failures were **host resource starvation**, not input size. Spawning a heavy
child process under a deadlocked GPU driver fails fast, and every retry from the client hit the
same wall, which is why it looked like a limit that persisted for ~2 minutes.

## Changes applied

In `internal-tool-planner.js`:

1. `stderr` is captured (bounded, last 1200 bytes) and logged as `codex_exit` with exit code and
   signal, so the next occurrence is explainable instead of silent.
2. Spawn failures log `codex_spawn_error` with the OS error code.
3. Transient `tool_engine_unavailable` retries twice (400 ms then 1200 ms) before failing the
   turn, so a brief host hiccup no longer aborts the user's request.

On the host:

- `harmonika-rtx-vision-tunnel.service` stopped and disabled: port 18189 is already served by
  `telco_engine`, so the tunnel could never bind and was restarting every 10 s (64k+ times).

## What still needs action

The 375 `D`-state processes are **unkillable by signal** (`kill -9` leaves them in place) because
they are blocked inside the AMDGPU DRM modeset path. They keep the load average at ~376 and will
only clear on **reboot**. Until then the platform works (all four core services active, chat and
tools verified end-to-end) but occasional fast engine failures remain possible; the retry added
above absorbs most of them.

Recommended when a maintenance window is available:

```bash
systemctl reboot   # clears the deadlocked amdgpu processes
```

After the reboot, verify:

```bash
cut -d' ' -f1-3 /proc/loadavg        # expect < 5
grep -c D <(ps -eo state=)          # expect single digits
journalctl -u harmonika-ai.service | grep -c codex_exit
```

Longer term, keep GPU-heavy services (browsers, Xvfb) off this host or cap them, because a stuck
`amdgpu` modeset deadlocks every process that touches the display path.

## Root cause #2 (the real one): Codex rejects input above 1 MiB

After the first fix the failures continued. Capturing `stderr` finally revealed the cause:

```
turn/start failed: Input exceeds the maximum length of 1048576 characters
(code -32602) {"input_error_code":"input_too_large","max_chars":1048576,"actual_chars":1094653}
```

The planner pipes the whole conversation into `codex exec`, and **codex itself caps a turn at
1,048,576 characters**. A long terminal session (many tool results) passes 1 MiB, codex exits 1,
and the planner reports `tool_engine_unavailable`. This is why the error looked like a limit that
never lifted: removing the 100 KiB API cap simply moved the wall from 100 KiB to 1 MiB.

### Fix: planner-level input compaction

`internal-tool-planner.js` now budgets the payload to **900,000 chars** and degrades context in
three passes, only as far as needed:

1. Clip every message except the newest user turn to 12,000 chars (head 60% + tail, with a visible
   marker marker `[... isi dipotong agar muat batas mesin ...]`).
2. If still too large, keep system messages plus the last 14 messages.
3. If still too large, shrink the newest user turn as well and trim `tools` to 24 entries.

The compacted payload is used for the prompt, for the tool-name enum and for response validation,
so the model never sees a tool it cannot call. Each compaction logs
`planner_input_compacted notes=[...] chars=N` for traceability.

### Verified

| Test | Before | After |
| --- | --- | --- |
| 1.6 MiB conversation (1,682,101 chars) | exit 1, `tool_engine_unavailable` | **200**, compacted to 254,589 chars |
| Travel project turn (`npm run lint`) | failed | **200**, answered "Lint: 7 error dan 2 warning." |
| Resumed session `ses_e633d83b5ffe871k5uvtbLpDs8` | failed | **200**, answered "OK" |
| `codex_exit` after the new process started | 3 in 40 s | **0** |

## Root cause #3: a stale LaunchAgent spawned the terminal every 10 seconds

`~/Library/LaunchAgents/id.harmonika.tui.gateway.plist` (left over from the previous Crush-based
terminal) ran `harmonika network serve` with `KeepAlive=true` and `ThrottleInterval=10`. The new
engine has no `network` subcommand, so it printed help and exited 1 — forever. The log held **10,790**
failed entries and a 231 MB binary was spawned every 10 seconds.

The plist was moved to `~/.config/harmonika-terminal/disabled-agents/` and unloaded; the loop stopped
immediately. Restore it only if a gateway service is genuinely needed again.
