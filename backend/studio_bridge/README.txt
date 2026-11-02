Harmonika Studio integration — 2026-11-01

Scope
Owner terminal integration. The hai.harmonika.id frontend is not modified by this
release. Existing image/video/generation routes are preserved. No TUI branding or
binary modification; no automatic updates; no running user terminal is stopped.

Data flow
TUI MCP studio -> chat.harmonika.id/v1/terminal/rpc -> existing license and signed
owner authorization -> studio_proxy.py -> private Linux adapter :18275 -> Studio
Windows HTTPS API. Vendor token exists only in /etc/harmonika-ai/studio.json (0600).
The adapter binds one authorized owner; other signed owners are rejected. This is
not a shared public multi-tenant Studio credential integration.

Terminal setup
Installed module: ~/.config/harmonika/video-tools/studio-mcp.cjs
MCP entry harmonika-studio in ~/.config/harmonika/harmonika.json, command node.
A terminal process that already loaded its MCP registry may need reopening/resume.
No need to paste a Studio key into the terminal conversation or frontend.

Examples
"Potong video /Users/id/Downloads/video.mp4 menjadi 10 detik dan simpan MP4."
"Cari jeda video ini menggunakan Auto Cut lalu tunjukkan rencana potongannya."
"Ekspor potongan ini sebagai draft CapCut."
Use studio capabilities first. Upload returns a local asynchronous job; poll it to
get result.asset_id. Editing actions return job IDs. Poll the same job; download
only after completed and result.artifact exists. Failed/unknown is not success.

Tool actions
capabilities, assets, upload(file_path), job(job_id OR request_id), download(job_id,
output_file optional), autocut/subtitles/direct/track/export_capcut/shorts(asset_id,
params), revise(params.timeline,instruction), render(params.asset_id,keep,...),
New video generation stays on the existing video tool/RTX route.

Readiness
Verified live through terminal RPC: capabilities, upload, assets access, Auto Cut,
render MP4, CapCut draft ZIP, status and download with SHA-256 validation.
AI Director, revisions, subtitles, Shorts, and tracking are wired
according to OpenAPI but their model output quality is not yet qualified. Existing
Studio queue may delay jobs behind other renders; no speculative new submissions.
MP4 upload currently limited to 500MiB. Transfers use 512KiB chunks through RPC.
Downloads support MP4 and ZIP. Larger/full production workloads are not load-tested.

Persistence and failure behavior
SQLite WAL stores owner/job/request mappings. Upload chunks support same-offset
idempotent replay; finish validates file length, MP4 signature and SHA-256. The
backend is the idempotency authority because the upstream API lacks this contract.
POST errors with uncertain outcome are not automatically retried. A recovered
remote job is polled only; an unknown POST requires operator reconciliation.
Use the same recorded request_id to recover a known submission. Never invent a new
request ID just because a request timed out. New same-content requests while a
prior one is pending return its job. Cached files and finished operational records
expire after 24h. Windows source assets/exports are not deleted by this adapter.
Local downloads are retained for the user. Private request payloads are operational
job data, not learning material or logs. No user prompts/media/tokens in logs.

Operations
Service: harmonika-studio.service (private 127.0.0.1:18275)
Code: /opt/harmonika-ai/studio-bridge/service.py
State: /var/lib/harmonika-studio (0700, file umask0077, 2GiB budget)
Proxy: /opt/harmonika-ai/video-routing/studio_proxy.py
Hot dispatch additions in video-routing/facade.py and crush-bridge/worker.py.
No panel, GPU, Windows, or existing AI service restart was needed for integration.
Only the new dedicated adapter was started/restarted during its installation.

Verification
python3 -m unittest test_service -v
node --test studio.test.cjs
MCP SDK listTools + callTool(capabilities), real upload/edit/download, anonymous401,
cross-owner denial, explicit same request_id returns same job, ffprobe MP4 validation.

Rollback
Remove only the harmonika-studio MCP entry (or restore saved config after checking
later edits), then disable the dedicated service once its jobs are terminal. The
existing facade/worker have .before-studio-20261101 backups; restore only the studio
hook if either file has been edited later. Preserve unresolved SQLite jobs. No
Windows gateway/ComfyUI/Studio restart or credential changes are necessary.

Guide/source mismatch: /cloud/video calls an external paid provider, not the RTX
gateway. This endpoint is not exposed by the Studio MCP. Use the existing video
tool for generation, then upload its saved MP4 to Studio for editing.
