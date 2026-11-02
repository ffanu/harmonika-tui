# Public API input limits

Status: the per-message cap on `/v1/chat/completions` is **removed** and applies to every token
(internal and public alike). Verified on the live service; measurements below.

## What is configured

| Knob | Env var | Default | Meaning |
| --- | --- | --- | --- |
| Per-message cap | `HARMONIKA_MAX_MESSAGE_BYTES` | `0` | `0` = no cap at all. Any positive value restores an explicit cap. |
| Request body cap | `HARMONIKA_MAX_BODY_BYTES` | `64 MiB` | The only remaining ceiling. It exists so one client cannot exhaust process memory. |

Both are read at startup in `runtime/public_ai_internal.go`. The validation that used them lives in
`public_api_docs.go` and now skips the comparison when the cap is `0`.

`GET`-side diagnostics report the effective values: `limits.max_message_bytes` is the string
`"unlimited"` when the cap is disabled, and `limits.max_body_bytes` reports the body ceiling.

## Measured behaviour (live, `chat.harmonika.id`)

| Payload | Before | After |
| --- | --- | --- |
| 150 KiB | `413 message_too_large` | **200 OK** |
| 400 KiB | `413 message_too_large` | **200 OK** |
| 500 KiB | `413` | **200 OK** |
| 700 KiB | `413` | **200 OK** |
| 900 KiB | `413` | **200 OK** |
| 1 MiB | `413` | `503 tool_engine_unavailable` |
| 3 MiB | `413` | `502 engine_error` |
| 8 MiB | `413` | `502 engine_error` |

## The real ceiling is the model, not the API

Above roughly 1 MiB the failure moves downstream: the planner logs
`pro_cs_preflight status=degraded reason=context_budget_or_shape` and the request ends as
`tool_engine_unavailable` or `engine_error`. That is the model context budget, not an API limit, and
no API change can raise it. One MiB of text is on the order of 250k tokens, far past a normal
context window.

Practical guidance for callers: send up to a few hundred KiB directly; for anything larger, chunk the
content and ask for a summary per chunk. The huge win is that ordinary work — long logs, whole source
files, pasted command output — no longer hits an artificial 100 KiB wall.

## Service resources

The web chat unit runs with `MemoryMax=2G` (drop-in
`/etc/systemd/system/harmonika-web-chat-live.service.d/message-limits.conf`). It idled at ~8 MB with a
~18 MB peak before the change; the extra headroom covers JSON decode of a 64 MiB body. If the body cap
is raised much higher, raise `MemoryMax` accordingly or the kernel will OOM-kill the service.
