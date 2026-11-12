# Native 720p video on the Windows RTX

Harmonika's owner TUI generates video through the Linux gateway, an authenticated
loopback coordinator, an SSH tunnel and a separate Windows ComfyUI runtime.
New requests use qualified Wan 2.2 profiles. Failed/unavailable high quality
requests return an explicit error rather than falling back to a 288p preview.
Existing video handles remain readable through the old routes.

| Component | Loopback address | Purpose |
|---|---|---|
| Linux coordinator | 127.0.0.1:18266 | Owner isolation, exact requested duration, segmentation |
| Linux SSH forward | 127.0.0.1:18262 | Windows high video broker tunnel |
| Windows broker | 127.0.0.1:18261 | Authentication, idempotency, GPU serialization |
| Dedicated ComfyUI | 127.0.0.1:18193 | GPU text encoder, denoising and tiled VAE |

The engine supports Wan 2.2 T2V with separate 14B high/low noise FP8 weights.
The qualified default on this 12 GB GPU is Wan 2.2 TI2V 5B FP16 for both text
and image animation; the 14B one-second diagnostic had visible particle
artifacts and is not promoted merely because its MP4 decoded successfully.
Image animation preserves the supplied reference. Both workflows export 1280×720 at 20
sampling steps. The 5B latent is 1280×736; export crops 16 pixels, without
upscaling. Requested durations longer than the qualified GPU segment are built
sequentially using the last frame as the next reference. Metadata reports the
actual models and segmentation. Seamless continuity is not guaranteed. These
raw videos are silent; use the existing Studio audio workflow for narration.
The TUI describes rendering scenes in concise English while replying in the
user's language. This is guidance for the existing assistant, not another cloud
service or a promise that English always fixes semantic errors. Exact product
appearance requires a supplied reference. Richer scene descriptions follow the
[upstream prompt-extension guidance](https://github.com/Wan-Video/Wan2.2#2-using-prompt-extension);
this integration does not enable Dashscope or download another Qwen model.

## Windows setup

Requires the existing ComfyUI environment, FFmpeg/FFprobe on C:, Python 3.12,
the existing private video toolkit broker/WindowsJob helpers and the RTX
Supervisor/ChildRuntime scripts in `backend/rtx_queue/windows`. Administrator
control is retained. D: is not required and is never brought online.

1. Copy this directory to `C:\Users\Kraken\harmonika-high-video`.
2. Copy `../video_toolkit/download.py` there as `pinned_download.py`. Configure
   the existing private wired interface selector if required by that downloader.
3. Provision `service.token` from the existing trusted video toolkit service
   token with inheritance disabled and access restricted to SYSTEM,
   Administrators and the operator. Never commit a token or put it in command
   arguments. Both ends must use the same token.
4. Run `python install_models.py` explicitly. It downloads only the pinned
   official Comfy-Org files in `models.json`, verifies all SHA-256 hashes and
   writes a receipt. Models live in `C:\HarmonikaVideo\wan22-models`. Existing
   model directories are preserved. Installation requires space for the model
   inventory, one largest-file temporary copy and a 10 GiB reserve.
5. Run `Install-HighVideoAutostart.ps1` once as administrator. It adds two
   SYSTEM boot tasks and registers them with the existing watchdog. Ports must
   be free; existing tasks/processes are never replaced or stopped.
6. Qualify real GPU jobs for **both** kinds. Each private job directory contains
   `input.json` (`family`, `kind`, `prompt`, `duration_seconds`), and for animation
   `input.png`. Run `python runner.py C:\HarmonikaVideo\jobs\<32hex>`.
   Use synthetic fixtures. Inputs and reference media must not go in QA logs.
7. Inspect representative frames and playback of both actual clips. Only when
   their visible quality is acceptable, run `python qualify.py --generate-job
   <32hex> --generate-seconds <tested> --animate-job <32hex> --animate-seconds
   <tested> --visual-reviewed`. It rechecks the checksum,
   resolution, duration and every decodable frame before atomically writing
   `quality-profile.json`. The workflow hash invalidates stale qualifications.
   Remove qualification fixture input files after recording mechanical proof.

Models are pinned to the [official Comfy-Org Wan 2.2 repository](https://huggingface.co/Comfy-Org/Wan-2.2).
The tested deployment and artifact evidence are recorded separately in
`docs/VIDEO-HIGH-QUALITY-REPORT-20261113.json`. Installed files alone do not
constitute a successful render.

## Linux setup and activation

Copy `coordinator.py`, `server.py`, `proxy.py` to
`/opt/harmonika-ai/high-video`, alongside the existing sibling
`/opt/harmonika-ai/flex-video/engine.py`. Install
`harmonika-high-video.service`; create a dedicated SSH forward from local
18262 to Windows loopback 18261 using the existing private tunnel identity.
Its Windows authorized-key entry must explicitly allow
`permitopen="127.0.0.1:18261"`, retaining its forced command and other
restrictions. Back up and modify only the matched public key entry; no sshd
restart or global firewall opening is required. Do not replace existing forwards. The service reads
`/etc/harmonika-ai/video-toolkit.token`; state is private under
`/var/lib/harmonika-high-video`.

Install `proxy.py` as `/opt/harmonika-ai/video-routing/high_video_proxy.py`;
the facade's hot-load hook runs only after existing owner authorization.
Verify authenticated capabilities with both qualified profiles, then create
`/etc/harmonika-ai/high-video.enabled` to activate. No gateway restart is
needed. Removing only this marker rolls back new submissions to the previous
route; do not roll back while new high quality jobs are pending.

Activate the signed native companion using `scripts/activate-native-video.py`;
see `runtime/video-control/README.md`. It preserves the branded native binary,
other MCP servers, provider configuration and owner permissions. New CLI
invocations read the updated configuration. Already running user sessions are
not terminated.

## Failure handling and privacy

SQLite WAL contains owner handles, request digests, timestamps, states and
technical error signatures only. Prompts and media are private temporary job
inputs, removed at terminal state. ComfyUI has metadata disabled; only owned
history/output is cleaned. Tokens never enter model process environments.
After a restart, abandoned private inputs are erased and unknown jobs await
reconciliation. Completed artifacts expire after 24 hours on both PCs;
idempotency tombstones prevent expired requests from rendering again. Existing
unrelated job directories are excluded. Admission is bounded by active queue
slots and disk reserves, rather than a quota counted per video segment.

Submission timeouts are reconciled with the same idempotency key; GPU work is
never blindly resubmitted. An unknown outcome blocks unsafe replay and keeps
opaque job handles for operator reconciliation. A busy GPU, unavailable PC,
missing weights or VRAM exhaustion remains an explicit error. Do not interrupt
global ComfyUI jobs, unload other models or reset drivers to make a test pass.

Verification:

```sh
python3 -m unittest discover -s tests/high_video -q
python3 -m unittest discover -s tests/video_routing -q
python3 -m unittest discover -s tests -p test_native_video_activation.py -q
node --test tests/native-video-companion.test.cjs
```
