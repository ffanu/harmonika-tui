# Isolated MiniMax H3 installation

Windows target: RTX 5070 12 GB, ~64 GB RAM. Dedicated root:
`C:\Users\Kraken\harmonika-h3-worker`. Existing Flux/vision workers, Python environments,
driver, firewall and routes must not be changed.

- `bootstrap.ps1`: pinned upstream launcher + ComfyUI, independent Python 3.12
  venv, Torch2.10/cu130 wheels. Does not execute upstream Pinokio scripts.
- `models.json`: pinned Comfy-Org model revision, exact sizes and SHA-256 values.
- `download_models.py`: bounded resumable range downloads + checksum verification.
  Initial set FL2VA + Qwen32B NVFP4 + two VAEs; Ref2VA separate after qualification.
- `smoke-workflow.json`: core-only synthetic 256x256/107-frame/2-step diagnostic,
  not a quality benchmark. No external APIs, user media or customer history.

Pinokio GUI is not required by the backend API; source launcher is preserved for
traceability. Some upstream scripts assume Python3.10 binary wheels and undocumented
custom nodes. The standalone runtime deliberately avoids those optional additions.

Status is in install/model receipts. An installed environment is not a verified
video engine until an actual video is generated and inspected. Do not register it
as ready in the shared media router before verification. No auto-update.

The original D: attempt was stopped after file-operation timeouts. Its partial
model chunks are preserved; no unrelated files or existing workers were deleted.
The active runtime uses C:, with an assembly-aware disk budget including a 20GiB
OS reserve. Inference coordinator runs only after environment and SHA receipts.
It uses a private ephemeral loopback port, offline/core nodes only, 4 CPU threads,
7GiB GPU reservation, CPU VAE and bounded startup/inference deadlines. A memory or
GPU headroom failure stops only the newly started ComfyUI process and reports
`degraded`; it does not alter/interrupt Flux or old vision workers.

Installed verification (2026-10-27): all four core model files and three CUDA
wheels passed their pinned SHA-256 checks. `pip check` passed; the environment
reported Torch 2.10.0+cu130, CUDA 13.0, RTX 5070, CUDA available. The PowerShell
inline probe was replaced with `probe_environment.py` to avoid native argument
quoting ambiguity. A private ComfyUI 0.43.0 instance accepted the synthetic core
workflow. Actual artifact qualification is tracked separately and must not be
inferred from this startup success.

Own one-off Windows tasks: `Harmonika-H3-bootstrap-20261027`,
`Harmonika-H3-download-20261027`, `Harmonika-H3-runtime-20261027`. Runtime runs as
the signed-in Windows account at limited privilege; installation tasks do not
modify any existing environment or driver. No recurring schedule or gateway
restart is installed by this module.

Final smoke result: MP4 H.264, 107 frames, 256x256, 4.458333 seconds, AAC stereo
32kHz; 173078 bytes. SHA-256:
`6437ac3ec1d89c2adc128dfc0bf9be9e5ac119f9a1e93bb85d210f927b46fb2b`.
Render took 250.27s. Entire media decoded with FFmpeg without error. First and
middle frames show the requested blue cup on a wooden table. This 2-step test
proves runtime operation only; high-quality settings, I2V and Ref2VA are unqualified.
Artifact copies: `docs/artifacts/minimax-h3/`. No shared gateway route is enabled.

Runtime URL for the Windows machine: `http://127.0.0.1:60887` (this run only).
The ephemeral port is recorded in `qualification-status.json`; it is not a public
endpoint and may change when rerun. Current runtime task has no recurring trigger.
After the render, observed GPU memory returned to 6463MiB used / 5480MiB free and
system RAM free to 36.27GiB. No legacy worker was restarted or interrupted.

Codex performed installation and qualification. OpenCode supplied a read-only
upstream compatibility review. The original Pinokio launcher is retained as source;
no Pinokio desktop UI or optional third-party custom node suite was installed.
