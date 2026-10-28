# Harmonika Media Suite — private runtimes installed

Four isolated adapters for the two-PC architecture. No change to existing API,
TUI binary, Flux/H3/vision, CS, GPU drivers, firewall or VNC profiles.

| Component | Operation | Execution | Current readiness |
|---|---|---|---|
| faster-whisper 1.2.1 (Whisper-WebUI engine) | audio.transcribe | Windows CPU INT8, small multilingual | Real target test passed |
| AuraSR 0.0.4 + v2 weights | image.upscale | Windows CUDA, 4x overlapped, one tile/batch | Real target test passed |
| Browser Use 0.13.14 | browser.inspect | Linux Chromium, new disposable profile | Real target test passed |
| F5-TTS 1.1.22 | audio.synthesize | Windows CPU, English/Chinese research only | Research render passed; commercial weights absent |

The full Whisper-WebUI/Gradio dashboard is not installed by this package: the
underlying faster-whisper engine is integrated as a headless adapter. Browser
navigation inspection is implemented; autonomous LLM clicks/form workflows are
not yet implemented or advertised. Existing bridges remain unchanged.

## Installation

Use a **Python 3.12** interpreter, and a new private target directory. Installer
rejects existing nonempty targets, symlinks and insufficient disk reserve.
No install action runs by default; `plan` is read-only. Pins in `models.json`
include immutable HF revisions, byte sizes and SHA-256 for every selected file.
Top-level packages are version pinned; transitive package hashes are captured by
pip install reports, not claimed as a fully hermetic dependency lock.

```text
python install.py plan --role windows
python install.py install --role windows --root C:\Users\Kraken\harmonika-media-worker
python install.py install --role linux --root /opt/harmonika-ai/media-suite
```

`--include-tts-research` downloads NC research weights and enables only the private
research worker. It never enables a commercial/public TTS route. No Indonesian
TTS qualification is claimed. Do not reuse a customer's voice in smoke testing.

Keep each venv separate: `.asr`, `.upscale`, `.tts`, `.browser`. CUDA Torch is
installed only in the new upscale/TTS venvs; no global or legacy environment is
modified. ASR uses CPU INT8, avoiding CTranslate2 CUDA12/cuDNN versus Torch CUDA13
DLL conflicts on RTX50xx. AuraSR checks 4GiB free GPU memory before work.

## Actual qualification before readiness

`qualify.py` accepts JSON with `synthetic_fixture:true` and `input` as below.
Each operation remains unavailable until its actual test passes. Run qualifier
with the relevant installed venv; use expected text for ASR. This operator test
is separate from unit/fixture tests and cannot be replaced by import success.

```text
.asr/python qualify.py audio.transcribe synthetic-asr.json --expected-transcript "Harmonika"
.upscale/python qualify.py image.upscale synthetic-image.json
.browser/python qualify.py browser.inspect synthetic-browser.json
.tts/python qualify.py audio.synthesize synthetic-tts.json
```

The interpreter paths above are schematic: Windows uses `Scripts/python.exe`,
Linux uses `bin/python`. Models are rehashed before each qualification.

## Private async API

Start `service.py` with the `.asr` venv on Windows or `.browser` venv on Linux,
only after actual qualification. Default ports are **18231** (Windows) and
**18230** (Linux); binding is loopback only. Startup fails if a port is occupied.
The installer does not register/start services or expose ports automatically.

All routes require `Authorization: Bearer <service.token>` (server-side only).

- `GET /health`: actual per-operation qualification, role, pending count.
- `POST /v1/jobs`: `{skill_id,input}`, plus `Idempotency-Key` (8–128 safe chars).
- `GET /v1/jobs/{job_id}`: state/result, bound to authenticated principal.

Inputs:
- ASR: `{audio_base64,language:"id"}`; WAV/FLAC/OGG/MP3, max8MiB/120s.
- Upscale: `{image_base64}`; PNG/JPEG/WebP static, 64–1024 px, max1MP.
- Browser: `{url:"https://example.com"}`; exact operator-registered host,
  HTTPS443, no query/credentials, public DNS only, isolated profile.
- TTS: `{text,reference_audio_base64,reference_text,language:"en"}`;
  operator research config required, reference 3–15s, output max60s.

Adapter `client.py` only accepts loopback worker/tunnel addresses. Credentials
never leave the backend. POST transport timeout is `submission_uncertain`; do not
retry as a new job. Poll/reuse the same idempotency key after operator recovery.
The internal adapter has passed cross-PC checks. No public/TUI automatic routing is activated.

## State, limits and privacy

One child job at a time, eight pending, 1000 metadata rows. WAL SQLite contains
technical IDs, hashes, state, timestamp, sanitized failure code and latency only.
No prompt/audio/image/transcript/browser cookies are written to diagnostic logs.
Payload/result RAM is bounded; last8 results expire after30min and disappear on
restart. Queued/running jobs at restart become `unknown`, without automatic
execution. This is explicit at-most-once recovery, not resumable inference.

Windows child launch uses a suspended process assigned to an owned Job Object
before resume, so timeout cleanup includes venv launcher descendants. This native
path must pass the actual Windows smoke test before service activation. Linux
cleanup targets only the process group created by that invocation.

Browser inspection does not reuse logged-in sessions or submit forms. Host
allowlisting is not a replacement for a network sandbox; do not expose this
private worker to arbitrary public browser jobs. A later autonomous action bridge
needs separate workflow/credential/approval integration and network isolation.

## Licenses and sources

- ASR engine MIT: https://github.com/SYSTRAN/faster-whisper
- Whisper-WebUI reference: https://github.com/jhj0517/Whisper-WebUI
- AuraSR code CC-BY-SA-4.0: https://github.com/fal-ai/aura-sr
- AuraSR-v2 weights Apache-2.0: https://huggingface.co/fal/AuraSR-v2
- Browser Use MIT: https://github.com/browser-use/browser-use
- F5-TTS code MIT: https://github.com/SWivid/F5-TTS
- F5-TTS weights CC-BY-NC-4.0: https://huggingface.co/SWivid/F5-TTS

Dependencies remain separate upstream packages with their own notices. No claim
that a model license alone licenses all components or certifies output rights.

## Local checks

`python -m unittest discover -s tests/media_suite -v` from repo root (aiohttp
required). Real qualification on both target PCs is a separate acceptance gate.


## Installed state — 2026-10-27

- Linux: `/opt/harmonika-ai/media-suite`, user `harmonika-media`, private HTTP
  `127.0.0.1:18230`, unit `harmonika-media-suite.service`.
- Windows: `C:\Users\Kraken\harmonika-media-worker`, private HTTP
  `127.0.0.1:18231`, limited interactive task `Harmonika-Media-runtime-20261027`.
  Task starts at this user's Windows logon; an offline/logged-out PC is unavailable.
- Dedicated SSH tunnel unit `harmonika-media-rtx-tunnel.service`: **Linux18232 →
  Windows18231**. Linux18231 was already occupied and was left untouched.
- Tunnel key is separate, forced-command/forward-only, only permits
  `127.0.0.1:18231`; old Windows keys and existing tunnel units are unchanged.
- Worker bearer token stays in `/etc/harmonika-ai/media-suite-rtx.token` on Linux.
  Never copy this credential into a TUI/API release or customer app.
- Browser Chromium files are root-owned under `/opt/harmonika-ai/media-chromium`.
  A single AppArmor profile permits namespaces only for that pinned executable.
  Global `apparmor_restrict_unprivileged_userns` remains1; sandbox stays enabled,
  with `--disable-gpu` and `--disable-software-rasterizer`. No global apt install.
- Upstream Browser Use's convenience installer was replaced with pinned
  Playwright1.56 browser-only download, avoiding its `--with-deps` global action.
- AuraSR0.0.4's actual release API has no `device` argument in `from_pretrained`;
  the adapter uses its CUDA default after an explicit availability/memory check.
- F5 uses its tensor inference API with already decoded, bounded PCM audio. This
  avoids its convenience reference-preprocessing downloads and TorchCodec/FFmpeg
  DLL coupling. No upstream package source was patched.
- Native Windows Job Object containment and descendant timeout cleanup passed.
- Actual synthetic checks: Indonesian ASR1.734s, 256→1024 AuraSR8.266s,
  browser1.783s, English research TTS43.328s (WAV3.093s/24kHz/mono).
- Public API routes, APK/iOS endpoints, TUI binary and legacy GPU workers were
  not changed. `browser.inspect` reads navigation/DOM; it is not an autonomous
  click/form agent. F5 research readiness is never commercial readiness.

Evidence: `docs/artifacts/media-suite/` and
`docs/MEDIA-SUITE-ACTIVATION-REPORT.json`. Local23 contract tests are distinct from
real target and cross-PC REST tests.


For Ubuntu23.10+ first installs, qualification may require an operator-created
AppArmor profile for a root-owned Chromium path, as applied above. The generic
non-root environment installer does not modify AppArmor or register services.
Do not disable global namespaces or launch Chromium with `--no-sandbox` to bypass
that acceptance gate. Current installation has completed this operator step.

On this Windows machine Torch wheels were reused read-only with `--torch-cache`
from the existing H3 wheel directory, with every cached file rehashed against
`torch-wheels.json`. No old environment was reused or modified.


## Licensed API + TUI routing active

The existing authenticated endpoint `POST https://chat.harmonika.id/v1/terminal/rpc`
now accepts these additional fixed operations (the original Go token/scope/quota
checks and Python single-flight lock remain intact):

```json
{"operation":"media_capabilities"}
{"operation":"transcribe_audio","audio_base64":"BASE64","language":"id"}
{"operation":"upscale_image","image_base64":"BASE64"}
{"operation":"browse_page","url":"https://example.com"}
```

This activates the installed suite through **terminal RPC**, not a new REST path.
The legacy Node `/v1/audio/transcriptions` handler was not replaced/restarted.
No frontend-specific chat buttons or new automatic web-chat planner were added.
API clients can invoke these operations; TUI agents select the registered MCP
file/browser tools from their normal tool list. Token scope and quotas still apply.

TUI helper release2026.10.27.8 is signed and active, with the same binary and session
files. New tools: `media_capabilities`, `browse_page`, `upscale_image`; the existing
`transcribe_audio` uses the private backend for supported files up to8MiB. With no
licensed transport configured it retains the existing local-ASR path. After an
ambiguous POST/poll/SSH failure it never falls back or retries automatically.
Open a new TUI session to load the new tool list; existing sessions were not stopped.

F5 remains accessible only to the authorized private research worker. The public
facade hard-rejects `synthesize_speech`/`audio.synthesize` with `license_restricted`,
including internal-tier tokens on the public endpoint. A client cannot opt into NC
research privileges by setting a request field.

Public browser targets are explicitly registered: example.com, harmonika.id,
github.com, docs.python.org, developer.mozilla.org. No account login, cookie reuse,
autonomous clicks or form submissions are enabled. A new domain needs operator
registration rather than silently permitting arbitrary internal/external targets.

Image worker deadline180s; facade poll deadline195s plus bounded health/submission
calls stays inside the gateway230s and TUI240s limits. Busy/private queues may still
produce `media_job_pending`; that is an uncertain operation, not permission to
submit a new job. No private job ID is exposed for another caller to poll.

Evidence: `docs/MEDIA-SUITE-ROUTING-REPORT.json`. Public live checks covered ASR,
1024px PNG output, DOM reading, auth401, and public TTS license rejection.
