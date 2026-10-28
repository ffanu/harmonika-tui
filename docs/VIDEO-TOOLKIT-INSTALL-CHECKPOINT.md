# Installation checkpoint — 2026-10-28

Task: install WanGP/Wan1.3B, FramePack F1 and research-only MMAudio for Harmonika.
Codex implementation; OpenCode read-only source/adapter reviews completed under
/tmp/harmonika-three-engines-review.md and /tmp/harmonika-framepack-sequential-review.md.
No Claude. No Git commit/push. Do not claim all engines ready yet.

## Current concrete state

- Three independent C environments installed, pip check + imports/CUDA probes pass.
- 55 pinned model files (~62.3GiB). Seven native Wan support assets were missing
  from the initial plan; they were added and SHA verified by supplement.py.
- MMAudio actual synthetic qualification VERIFIED: job
  6abf24657d96445d84a7e86a6f2ef3cd; 50.02sec; 8.010884sec mono44100Hz;
  composited MP4 ffprobe8.041667sec, H264320x192 +AAC stereo. Private research only.
- Local evidence outside Git: /Users/id/.local/share/harmonika/video-toolkit-research/
  6abf24657d96445d84a7e86a6f2ef3cd/result.{wav,mp4}. WAV SHA
  38b5411f7895729de598dd1f1d717932e542f68585825ccefb05da04c71804da.
- Wan full API startup ready. Synthetic render VERIFIED in job
  1c74284b55614b2d9adb71ad23e9f000; qualified target1sec50steps832x480;416.11sec cold startup.
  Operator API job b03b88a9670eeb92d5c7783e9ccbc65d also completed in114.05sec;
  duplicate idempotency key returned one job. Actual MP4 retrieved through Linux
  client.download with SHA verification. Source input files/native temp removed.
  Video visually inspected: white router/natural colors, no rainbow in this sample.
  Earlier failed fixtures archived mechanically after fixing missing startup
  dependencies, removing unsupported num_generations and adding CLIP/VAE assets.
- FramePack model download ongoing; real render NOT yet qualified. Target1sec25steps.
- 14 focused local contract/privacy/ownership/restart/license tests pass.

## Runtime locations

- C:\HarmonikaVideo\{wangp,framepack,mmaudio}\{app,.venv}
- NEW private jobs C:\HarmonikaVideo\jobs (NTFS ACL restricted to Kraken/System/Admin).
- Control scripts/status: C:\Users\Kraken\harmonika-video-toolkit-source
- Large pinned public model cache D:\HarmonikaAI\video-toolkit\models (D is exFAT).
- Older synthetic failed jobs under D:\HarmonikaAI\video-toolkit\jobs preserved.
- Old partial D environments/bridge remain preserved; unused. H3/Flux unchanged.
- Do not call Path.resolve on D cache paths: very slow. Junction comparison now
  uses os.readlink and lexical normalization. C app/ckpts junctions into D cache.
- T5 is now canonical umt5-xxl/models_t5_umt5-xxl-enc-quanto_int8.safetensors.
  Linker recognizes only its exact SHA/size/revision original-root receipt alias.

## Own current processes / controllers

Read *-owned-process.json and compare PID, create_time and exact command before
any action; never blindly reuse these example PIDs.
- Downloader31724: owned GET-only process, resumed after own old43660 stopped.
  download.py now uses read1,30sec socket timeout,180sec chunk deadline, partial
  resume,12 threads64MiB, SHA check/atomic assembly and exclusive download.lock.
- Coordinator44248; serial synthetic qualification, GPU lock, no failed rerender
  without an explicit corrective step. Loop stops after3h (check before it expires).
- Private broker440, Windows loopback18237. No public route. MMAudio hard blocked.
- Supplement18844 completed7assets. Do not restart it alongside main downloader:
  main now reads55rows including those assets (old process read48 rows).
- Some old own blocked copy PID6732 was termination-requested; do not touch any
  unverified or unrelated process. Never kill agent/SSH/terminal processes.

## Linux operator bridge (DEPLOYED, authenticated tests passed)

SSH alias vps=root@103.157.24.189:22023, configured key auth.
- /opt/harmonika-ai/video-toolkit/client.py
- /etc/harmonika-ai/video-toolkit.token (600; never print/read into repo).
- /etc/harmonika-ai/video_toolkit_tunnel_ed25519 (dedicated, private).
- harmonika-video-toolkit-tunnel.service ACTIVE: Linux127.0.0.1:18240 ->
  Windows127.0.0.1:18237 via SSH22022. Forced command key cannot open a shell;
  only permitopen18237. Existing Windows authorized keys preserved.
- Tests pass: health authenticated,401 unauthenticated,owned artifact404,
  structured offline degraded,license_restricted audio,forced shell denial.
- Existing main PIDs unchanged: AI1487202,Go1379498,master1327592,hai1540280.
- No existing service restart, hms-sstp untouched, no public paid API/TUI route
  registered. Earlier WebUI stage restart has NOT been approved; don't activate it.

## Access helpers (temporary local)

/Users/id/.config/harmonika-terminal/ssh-runtime/bin/python has Paramiko.
/tmp/harmonika-h3-ssh.py connect() uses pinned Windows hostkey from Linux knownhosts;
Windows kraken@103.157.24.189:22022, password via HARMONIKA_RTX_PASSWORD environment
(user supplied). Never persist password in source/logs. Explicitly close channels.
/tmp/harmonika-media-linux-control.py reads Linux commands from stdin.
Windows base Python C:\Users\Kraken\AppData\Local\Programs\Python\Python312\python.exe.
For psutil diagnostics use existing H3 .venv Python read-only, never change H3.
Detached children require CREATE_BREAKAWAY_FROM_JOB|DETACHED_PROCESS|NEW_PROCESS_GROUP.

## Next verification

Read live qualification/model-download statuses; inspect failed synthetic diagnostics
only. Wait for current Wan render; its artifact is already copied/ffprobed/visually inspected.
Watch FramePack download, then real qualification; fix only concrete failures,
preserving no automatic retry on unknown jobs. Save final honest readiness report.
WanGP Community License2 permits in-house outputs but no paid/white-label API here;
MMAudio official weights CC-BY-NC4 remain research-only. Data firewall: no customer
history/media/prompts/secrets in logs or SQLite; only synthetic fixtures used.

## FINAL — all three qualifications passed

Supersedes pending notes above. Main downloader completed55files, all SHA verified,
1793124189UTCepoch. FramePack job9b7fe7709afc4198869ffcadfb41ae1e completed in1450.36s:
832x480/30frames/1sec/25steps, SHA
 dea502dcde4b48988680e7e905ecb8cedf3eb9ce3c139d15c18b4890e8ef7602.
Mac artifact docs/artifacts/video-toolkit/framepack-smoke.mp4 fully decoded and
visually inspected; it preserves the illustrated reference. Wan artifact likewise
fully decoded/visually inspected. MMAudio evidence stays outside Git (NC research).
19 tests pass. Broker now PID43796, own receipt authoritative; updated source adds
single-instance lock before DB mutation, native safe resource errors and6GiBFramePack
VRAM preflight. Actual duplicate broker refused, no stderr. OpenCode runtime review
finished exit0; normal cold I/O, no retention bug shown; default25steps and parent
40min timeout already cover noted residuals. No GPU rerender needed for preflight.
Final Linux capabilities: Wan and FramePack operator_ready=true, MMAudio
research_cli_ready=true/commercial=false, all public_route_enabled=false.
Existing Linux core PIDs unchanged. Windows autostart not configured. No public
routing activation or old WebUI restart; no push. See INSTALL-REPORT and JSON.
Final monitor and OpenCode review exited normally. Two earlier own local monitor processes were stopped after correcting an EOF polling-loop bug; no user or agent processes were signalled. No other work follows.
