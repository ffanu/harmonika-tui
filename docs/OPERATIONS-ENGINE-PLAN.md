# Operations engine checkpoint — 18 November 2026

Users: authenticated Harmonika API/TUI users working in explicitly registered
projects; administrators inspect aggregate license usage. No new product menu.

Flow: existing `/v1/skills/invoke` authentication/quota → master trusted principal
→ private loopback operations service → exact handler → scoped verification.
The client never chooses a principal, secret file, host executable or server path.

Acceptance:
- `engine.diagnose`: bounded real probes, classified failures, metadata only.
- `skills.selftest`: real scoped checks with positive counts and explicit skips.
- `job.resume/reconcile`: durable identity and CAS; unknown effects never replay.
- `security.scan`: actual Semgrep/Trivy; unavailable DB/tools remain degraded.
- `browser.regression`: actual assertions and private bounded Playwright traces.
- `database.audit`: registered DB, read-only schema/index/query plan inspection.
- `deploy.preview/apply/rollback`: reviewed plan, actual health, guarded recovery.
- `license.usage`: own key by default; global aggregates administrator-only.

Q&A: private logs retain mechanical counters/status/digests only. No chat, source,
SQL text, tool stdout or credentials in durable jobs/telemetry. Tests use isolated
projects, databases and HTTP servers. No BGP/core, billing/CS mutations, production
release switch, or user process termination during qualification. Activation and
GitHub delivery require tests, source hashes, backup, permission-aware service
handling and a signed portable package. Catalog registration is not qualification.

Checkpoint: implementation in progress; readiness and limitations will be recorded
in the final audit after actual integration and cross-platform tests.
