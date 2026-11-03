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
