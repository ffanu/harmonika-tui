# TURN B: additive schema v2 and recovery

Continue the same project/session; inspect CHECKPOINT.json, user edits and actual
staging first. Add priority low|normal|high (default normal), version integer >=1.
Migrate existing DB additively with PRAGMA user_version=2, preserve every old ID,
owner, title, description, status, request receipt, attachment bytes and session.
Migration reruns idempotently; v1 code must still run against v2 schema without
reset/downgrade. New creates include priority; old creates without it still work.
POST canonical idempotency includes normalized priority; requests from v1 must
remain replayable unchanged (normal default), never return a second ticket.
PATCH now REQUIRES version (integer NOT bool); only update status/priority if it
matches current row. Successful update increments version exactly once; stale
version ->409 {error:'version_conflict'} with no mutation. Missing/invalid ->400.
GET/list/create/reconcile include priority and version. Add #priority to create form
and .priority select in each ticket row. UI sends the version it actually read.

A response for a session that has since logged out must not repopulate its rows.
Lost-ACK UI: each create generates one Idempotency-Key; network loss triggers
reconciliation of that exact key, bounded at most3 GET checks with delays, without
another POST. Show recovered state or explicit uncertainty, retain key while
uncertain; do not auto-create again. Busy503 must show retriable error with no
false success or unbounded loop. Manual retry after lock release may reuse the
same unchanged request key; changed payload needs deliberate new request.

Install/test before immutable app-v2 build. Activate ONCE key stage-v2. Verify
browser smoke plus tools/ui-check.cjs --case lost-ack and --case locked (these arm
only owned proxy/DB faults). Preserve Migration sentinel and attachment from TURN A.
Then activate bad-v3 ONCE key challenge; prove health200/ready503; rollback CODE ONLY
to app-v2 key rollback, keep DB volume. Verify readiness, data and schema again.
Write RESULT.json exactly {"scope":"isolated_linux_docker","schema_version":2,
"active_release":"app-v2","same_session_resume":true,"rollback_performed":true,
"uncertain_write_reconciled":true,"production_application_changed":false}.
Write HASIL.md with actual test/browser evidence, checkpoint/image, recovery,
remaining gaps and limits. No claims of independent grader passing.
