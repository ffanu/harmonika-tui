# Browser macro coordinator

`desktop_browser(action="macro")` executes explicit typed steps through the owner's
local Chrome adapter. Linux coordinates command/check ordering, a per-device/tab/
origin lock, deadline and a SHA-256 plan. It reuses the configuration coordinator's
tested pure sequencing engine and guarded stdio client; it never executes shell
commands on behalf of a page. The browser worker validates opaque hash envelopes.

Supported steps: `navigate`, `type` (search), `select` (filter), `set_checked`
(filter), `reveal`, `click`, `read`. Every step requires `expected_text`; optional
`expected_rows` checks the complete first table, including headers if present.
Control values are checked after search/filter changes. Click postconditions must
identify newly appearing text or changed expected rows; an already satisfied
condition is rejected before clicking. Exact unique labels are
required. `frame` identifies a same-origin iframe by its exact HTML ID. Cross-origin
frames, raw JavaScript, login/OTP, payment, posting and destructive actions remain
outside this engine. Snapshot/action references are short-lived and single-use;
semantic relabeling, disabled controls and obscured clicks are rejected.

`dry_run` defaults to true. Set it to false to run an authorized plan. Only fresh
observations and postcondition checks are polled; actions are never automatically
replayed. On uncertain dispatch, stop and reconcile. This is not persistent
cross-request idempotency: do not resubmit an uncertain macro. It records an
explicit plan, not a background recording of the user's browser.

```json
{
  "action": "macro",
  "tab_id": "123",
  "start_url": "https://example.test/devices",
  "dry_run": false,
  "steps": [
    {"action": "select", "label": "Status perangkat", "value": "offline",
     "expected_text": "Daftar perangkat"},
    {"action": "set_checked", "label": "Hanya prioritas", "checked": true,
     "expected_text": "B2 Beta offline", "expected_rows": [["B2", "Beta", "offline"]]}
  ]
}
```

Install `worker.py` and the canonical `backend/config_transaction/engine.py` as
`/opt/harmonika-ai/browser-macro/{worker.py,engine.py}`. Private SSH alias `vps`
starts the worker on demand. There is no new listening port or daemon restart.
Local adapter files are `desktop_browser.py`, `browser_audit.py`, `browser_macro.py`,
`config_transaction_client.py` and `config_transaction_engine.py`.

The SQLite WAL journal `/var/lib/harmonika-browser-macros/mechanics.sqlite` stores
only timestamp, skill_id, error_type/status and latency. Full steps and page data
remain local; the coordinator receives hashes instead of URLs, labels, searches,
table contents or chat history. Failure messages are technical signatures only.
Browser evidence is returned privately to the caller, not placed in shared memory.

Actual browser execution has been verified on macOS Chrome. Windows/Linux desktop
adapters and raw VNC control are separate capabilities, not implied by this result.
