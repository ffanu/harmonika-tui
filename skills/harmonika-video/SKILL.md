---
name: harmonika-video
description: Generate actual text-to-video or image-to-video clips on the local Windows RTX engine, then poll and download the verified MP4. Indonesian video/vidio/animasi requests.
---

Discover the advertised `video` MCP tool across all namespaces. The owner native
terminal exposes it through `harmonika_video`; the older `harmonika` namespace
only contains `video_studio`, which edits existing files. An empty search in one
namespace does not prove video generation is absent. Do not run legacy
`harmonika catalog ...` commands in the native v2 CLI.

1. Call `video` with `action=capabilities`. Report a real unavailable/busy result
   accurately; a service registration or old qualification is not a render.
2. Describe the rendering scene in concise English, preserving user intent, names,
   quoted text and exact duration. Include concrete visible subject details.
   Reply in the user's language. Exact product appearance needs an actual supplied
   reference; do not claim an invented object is a faithful product replica.
   For a text request, call `action=generate` with the user's prompt and exact
   `duration_seconds`. For an explicitly supplied image, use `action=animate`
   and `reference_file`. Never silently substitute text generation for the image.
3. Submit once. Retain the returned `request_id`/`job_id`, then `action=status`
   on that same ID. Respect `poll_after_seconds` between checks with a bounded
   wait tool when available; do not repeat unchanged progress before every call.
   If no wait/notification tool is available, stop after three status checks in
   this turn and report the pending ID honestly; never spin in a tight tool loop.
   Queued/running/uncertain are not completed. Never duplicate
   GPU work after a timeout or create a new request to bypass a failure.
4. Once completed, `action=download` saves a checksum-verified MP4 in
   `~/Downloads/harmonika-ai/video/` by default. Claim a local file only after
   download succeeds. Include its clickable path, actual duration, and quality.

Read the live quality/model/resolution fields. When the qualified high profile
is active, render and report its actual native 1280x720 result. An old tool limit
or a historical preview qualification must not override current capabilities.
An unavailable high profile is an explicit error, never an automatic preview.
Generation is silent unless a separate narration step succeeds.
For requested speech use `action=narrate` on a compatible completed job; flexible
jobs may require Studio after download/upload. Never claim sound is present
from capability alone. Do not send a prompt-only substitute for an authorized
generation request when the real tool is available.

GPU work and model loading can take minutes. Respect pending job ownership and
resource limits. Do not interrupt another job, unload other models, restart
ComfyUI, or kill operator/agent processes. Private prompts/media/credentials must
not be copied into logs, shared memory, training data, or QA reports.
