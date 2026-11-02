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

## Sessions that contain tool history or media

A resumed terminal session sends its whole history, which includes `tool`, `tool-call`,
`tool-result` and sometimes `image_url` parts. Two rules used to fail those sessions forever with
`422 unsupported_media`, showing the same "Input teks melampaui batas" text as the size error:

| Input | Before | After |
| --- | --- | --- |
| `tool-call` / `tool-result` parts | `422 unsupported_media` | **200 OK** — rendered as `[tool name] {...}` text |
| `image_url` / `audio_url` / `video_url` / `file` parts | `422 unsupported_media` | **200 OK** — replaced by a marker such as `[media image_url tidak dikirim karena API publik hanya menerima teks]` |
| `attachment_ids` | `422 unsupported_media` | **200 OK** — attachments are ignored |
| unknown part type | `422` | `422` (still guarded) |

Rejected requests now log their redacted shape (`public_ai_input_rejected code=... shape=[...types...]`)
so a failing session can be diagnosed without logging any user content.

Verified end to end by resuming the previously failing session
`ses_e633d83b5ffe871k5uvtbLpDs8` (79 messages, one 335 KiB tool output, one image part): the reply
came back normally instead of the 422 error.

Note: text-only degradation means the model never sees the actual image bytes. For real vision use
the media tools (`analyze_image`), not the public chat endpoint.
