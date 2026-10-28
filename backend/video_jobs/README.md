# Private asynchronous video preview

PC1 is the CPU WebUI gateway. PC2 is Windows RTX, running a **separate** loopback
video queue at `127.0.0.1:18233`, forwarded to PC1 `127.0.0.1:18234` by the dedicated
`harmonika-video-rtx-tunnel.service`. The differing ports are intentional; the
forward-only SSH key permits only PC2 port 18233. Neither endpoint is public.
No drivers, existing Flux queues, customer databases or main gateway were changed.

`service.py` provides authenticated `POST /jobs`, `GET /jobs/{id}`,
`GET /requests/{idempotency-key}`, and `GET /jobs/{id}/file`. Bearer + gateway-issued
64-hex owner are required. Owner IDs are server-derived; clients cannot choose them.
Input is `{prompt, duration_seconds:10}` only. One GPU job runs at a time, four wait,
one active job per owner, ten submissions/day/owner. Only eight-step 320×192 T2V
is exercised. Reference images, voices, other durations/resolutions are unsupported.

H3 renders 243 frames at 24fps (10.125s); the adapter re-encodes exactly 240 frames
and fully decodes/verifies the resulting 10.000s H.264 MP4. **Preview is silent**;
no claim about audio or professional advertising quality. An installed model is
not readiness: see real artifact qualification below.

The existing H3 runtime must be healthy; if the PC/runtime is off, job fails with
`engine_offline`. No remote boot or legacy-engine preemption. An uncertain render
submission/deadline or worker restart holds the slot as `unknown`, rather than
silently re-rendering. Operator must reconcile the recorded Comfy prompt ID before
recovery. Windows service restart after a completed/failed job is safe. Never clear
someone else's Comfy queue or issue global unload/interrupt.

SQLite WAL holds timestamps, random job/owner/idempotency identifiers, input digest,
stage/error code, latency and artifact metadata. **No prompts/customer media/chat
history/secrets in logs or the job DB**. Prompts are bounded RAM only; restart marks
unfinished work unknown. Completed/failed artifacts expire after seven days;
unknown work is retained for operator review. 512MiB artifact and 1000-row ceilings.
Raw outputs under our own `harmonika-web-video/{job}_*.mp4` are removed only with
that expired job. Private outputs are not a training or general knowledge source.

## Qualification 2026-10-27

A real synthetic router/internet promotion render completed. The initial encoding
revealed a time-base bug (caught by full decode/duration assertion); fixing PTS to
`n` with time base `1/24` recovered **the same render without a GPU resubmission**.
Result: 320×192, 240 frames, 10.000 seconds, 828951 bytes, silent H.264.
SHA256: `0c238160343efc937cc7c0d857a563bbcabf6968b54dc4a757ecbde795d82be3`.
Initial render+failed encoding elapsed 746.11s; corrected encoding adds a few seconds.
First/middle/last frames show a router and smartphone with moving light streaks.
This is a low-resolution preview, not a high-resolution final campaign.

Pinned H3 weights/runtime remain those in `../minimax_h3/models.json` and
`sources.json`. Original license:
https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE
Commercial UI identifies MiniMax H3; public rollout must respect that license's
territory, user-terms and other service conditions. No claim of unrestricted
worldwide/open-source licensing. Private operator qualification is distinct from
unrestricted public release.

## WebUI source and verification

Source snapshot/patch: `/Users/id/Projects/harmonika-hai-video/` (no credentials,
customer DBs or production env). Isolated stage on PC1 `/opt/harmonika-ai/video-web-stage`,
loopback18235. Source changes: video intent branch, `video_web.py` owner proxy,
resumeable job marker, browser polling with timeout/backoff, HTML5 controls,
private verified disk cache and HTTP Range for mobile Safari. Generation is off
by default (`HAI_VIDEO_ENABLED=0`); staging explicitly enables it. No TUI route or
`/v1` API contract changed, and main services were not restarted.

Tests:
```
python -m unittest discover -s tests/video_jobs -v
```
Use an isolated environment with PyAV for the duration test. Also run WebUI bridge
unit tests and full staged Flask integration before activation. Browser tests use
synthetic fixtures; their reports distinguish mocked submission from real
cross-PC artifact status/download/playback. No credentials in receipts.
