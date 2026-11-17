# Durable delegation gateway

These Go files overlay the existing Harmonika web gateway package `main`:
replace `delegate_skill.go`, add `delegate_jobs.go`. Keep the existing authenticated
`public_ai_master.go` interceptor for `agent.delegate`. The tests compile against
that application; this directory is not a standalone Go module.

`POST /v1/skills/invoke` requires a Bearer token with `chat` or `skills` scope:

```json
{"skill_id":"agent.delegate","input":{"action":"submit","request_id":"stable-random-uuid","task":"Hitung 17*19","provider":"auto","timeout_seconds":120}}
```

Persist the random request ID before the request. Queued/running submissions
return 202 with `result.job_id`. Poll using `action=status` and either `job_id`
or the original `request_id`, never both. A repeated owner/request/payload returns
the same job without charging another admission. Changed payload is 409;
missing/foreign jobs are 404. Busy/full admission is 429 with Retry-After.

The token's verified database ID binds ownership; client headers cannot select
another owner. Status reads do not consume generation quota. `auto` tries
OpenCode then Codex after an observed failure within the original budget.
No Claude adapter is invoked. Delegation here is text-only, not a remote host
executor. Provider isolation flags and content guards do not constitute a
hostile-code OS sandbox. Preserve the existing provider authentication.

State defaults to `/var/lib/harmonika-ai/delegate-jobs-v1` (override with
`HARMONIKA_DELEGATE_STATE`), directory 0700, key/records 0600, HMAC authentication,
flock admission and fsync/atomic record writes. Tasks/output never enter this
ledger. Completed output lives in RAM and becomes `output_unavailable` after
gateway restart. Pending orphaned work stays `outcome_unknown`, blocking slot
reuse. A deadline never proves the physical worker stopped; no process signals
are sent. Two unresolved workers maximum; 1000 records admission maximum.
Only terminal completed/failed/timeout records older than 24 hours are pruned;
uncertain jobs require operator reconciliation. Idempotency is retained for that
24-hour window; never use an expired ID to retry uncertain effects.

Build/test in an isolated copy of the actual gateway sources:

```sh
go test -race -count=1 -run '^(TestDelegate|TestPublic)' ./...
go build -o harmonika-web-candidate .
```

Activation must compare original source and binary hashes, wait for no active
gateway work, keep a private backup, atomically install the tested binary, restart
only the actual gateway unit, and check health plus the authenticated async
contract. Preserve all other sources and services. Restore the original binary
and changed source files on a failed canary, only if their hashes still match
the installed candidate. Update `skill.py` and its SHA256 manifest together;
the skill runner intentionally returns `gateway_only` for direct execution.
