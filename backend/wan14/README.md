# Wan 2.2 A14B candidate and activation

Current qualification and native terminal evidence:
[`VIDEO-A14B-QUALIFICATION-20261113.json`](../../docs/VIDEO-A14B-QUALIFICATION-20261113.json).
The activated text profile uses four steps/5-second segments; the reference
profile uses guided 20-step/1-second segments. A native reference exam completed
in roughly six minutes per second of video on this RTX 12 GB; longer requests
remain supported through sequential assembly and cost proportionally more time.

PC 1 remains the authenticated Linux gateway/coordinator. PC 2 performs local
ComfyUI inference on the RTX 12 GB. This module reuses the existing contained
worker, shared GPU lock, owner authorization, idempotency, private temporary
inputs, 24-hour output expiry and metadata-only SQLite WAL ledger.

The text and image pipelines have separate high/low-noise experts. A14B has
approximately 27 billion total parameters and 14 billion active per step; it
does not mean a 120B model. The optional LightX2V LoRAs use four sampling steps
at CFG 1, Euler/simple and shift 5. Metadata reports the actual steps truthfully.
The guided reference preset uses the full 20-step experts at CFG 3.5, without
LightX2V LoRAs. CFG 1 bypasses negative conditioning; the guided preset can move
explicit English exclusions (for example `No people. No hands.`) into effective
negative conditioning. User-requested people/portraits are not globally blocked.
Reference fidelity and unrequested objects must be checked visually; a decoded
MP4 and preserved silhouette alone do not prove semantic compliance.
No inference runs while installing the service, and installation alone does
not qualify a model. Model files stay on C:; D: remains offline.

## Installation order

1. Preserve the existing `high_video/workflow.py`, its six-file manifest and
   qualifications. Candidate wrappers depend on the matching `high_video`
   broker/runner/server/proxy modules in this repository. Deploy them to the
   existing `harmonika-high-video` Windows and `high-video` Linux directories
   without stopping existing services. The Linux server is safe to import.
2. Create a private `C:\Users\Kraken\harmonika-wan14` directory accessible only
   to Kraken, SYSTEM and Administrators. Copy this module's Windows Python
   files, manifest, the existing `pinned_download.py`, and a private copy of the
   existing service token as `service.token`. Never put the token in Git.
3. Run `install_models.py` explicitly with the existing Windows Python 3.12.
   All files are revision-pinned and SHA-256 checked. The installer creates
   ComfyUI `harmonika-quality` junctions, including the LoRA directory. No
   downloads occur during an inference request.
4. Run `install_windows.py` once. It creates the dedicated loopback listener
   on 18263, a SYSTEM boot task with no execution deadline, and adds that task
   to the existing watchdog. It refuses to overwrite an existing task or
   conflicting candidate configuration. Administrator controls remain intact.
5. Add only `permitopen="127.0.0.1:18263"` to the exact existing restricted
   tunnel public-key entry; retain its forced command and other restrictions.
   Copy `bridge.py` and `server.py` to `/opt/harmonika-ai/wan14-video`, install
   the two supplied systemd units and enable/start them. They use loopback
   18264 for the SSH tunnel and 18268 for the coordinator. The private state
   directory is `/var/lib/harmonika-wan14-video` (0700).
6. Under the shared GPU lock, render synthetic text and reference-image exams
   with the candidate `runner.py`. Inspect representative frames, check full
   FFmpeg decoding and hashes, then qualify both completed jobs:

   Use `python qualify.py --generate-job ID --animate-job ID --generate-seconds N
   --animate-seconds N --visual-reviewed`, replacing IDs/durations with the actual
   completed evidence. Text and image segment lengths may differ.

   Evidence includes the actual workflow hash for each family. Source changes,
   missing LoRAs, wrong text/image family or stale receipts fail closed.
7. Copy `proxy.py` to `video-routing/wan14_video_proxy.py` on Linux. The facade
   must load it before the existing high/flex proxies. Only after qualification
   and both capability checks succeed, create
   `/etc/harmonika-ai/wan14-video.enabled`. Existing signed native TUI video
   tools discover the model through backend capabilities; no binary/UI changes
   are required. Verify a real native request through artifact download.

Requests longer than the qualified segment length use the existing frame-led
continuation/assembly pipeline. Exact total duration is retained, but seamless
transitions are not guaranteed. Video inference is silent; narration uses the
separate studio/audio pipeline. Do not silently replace failed new submissions
with preview video. Old job GET/download recovery can fall through on not-found.
Guided sampling is substantially slower on the RTX 12 GB. A one-second qualified
segment does not impose a one-second limit on the requested/assembled duration.

Rollback: remove only `wan14-video.enabled`; the verified prior high-video route
remains installed. Do not stop a worker while a GPU job is running or uncertain.
Do not cancel unrelated jobs or globally interrupt/unload ComfyUI.

Focused checks: `python3 -m unittest discover -s tests/wan14 -q` and
`python3 -m unittest discover -s tests/high_video -q`.

Upstream references: [Wan architecture](https://github.com/Wan-Video/Wan2.2),
[official ComfyUI templates](https://github.com/Comfy-Org/workflow_templates),
[Comfy model files](https://huggingface.co/Comfy-Org/Wan-2.2).
