# Shared SEO backend

Authenticated asynchronous adapter for fourteen existing SEO operations. The TUI
uses the signed internal owner RPC; API clients use the existing skill gateway
and trusted API principal. Neither client receives root secrets or browser OAuth.

Runtime: loopback 18260, `harmonika-seo-shared.service`. Two workers, an eight-job
queue, private per-principal projects, bounded artifacts and metadata-only SQLite
WAL. Submit requires a stable 32-hex `request_id`; recover by request/job ID, never
resubmit uncertain work. Report data lives in private owned artifacts, not logs.

Internal endpoints: POST `/capabilities`, `/invoke`, `/job`, `/request`, `/artifact`.
`/invoke` accepts `{skill_id,input,request_id}`. Artifact reads are chunked at 64KB,
owner-authorized and SHA-256 verified. Project paths are relative to the private
backend project; absolute local/host paths and symlink escapes are rejected.

Ten operations currently report available. Performance depends on live provider
availability. Search Console/GA4 require explicit per-principal Google property
authorization. Visual capture is blocked until CPU browser isolation is qualified:
`--disable-gpu` alone did not prevent a DRM access during testing. Do not enable
that path, change GPU drivers or reuse owner browser credentials as a workaround.

Production master registry integration is prepared in `master_engine/shared_bindings.py`;
activation requires the explicit restart exception described in AGENTS.md. The
owner TUI route is already live without restarting the core gateway. No broad
public video/media entitlement is granted by this SEO adapter.
