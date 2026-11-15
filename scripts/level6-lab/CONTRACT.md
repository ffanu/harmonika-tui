# Level 6 — durable job recovery on an owned Linux lab

This is an examination, not a production service or an RTX test. Implement
`engine.py` and `Dockerfile`; supplied API transport, executor, tools and tests
are immutable. Use Python stdlib only. Keep the pinned FROM, offline build,
USER 10001:10001 and CMD ["python", "/app/server.py"]. Build context is exactly
engine.py, server.py, effect_connector.py, Dockerfile. Do not edit protected files.
No direct SSH/Docker, process signals, external accounts or package downloads.
Use only tools/ops.py for the registered staging. Write your own additional tests.

## Core interface

Export Queue(path, executor), start(), stop(), submit(owner,key,payload),
get(owner,job_id), cancel(owner,job_id), events(owner,cursor), artifact(owner,id).
All HTTP-facing methods return (status, JSON dict), except artifact returns
(status, bytes), events returns (status, {events:[...]}). stop joins own workers
with a bounded wait; do not manufacture cancelled/completed states on timeout.
The server creates Queue('/data/jobs.sqlite', EffectConnector('/data')).
No work may run at import time. Threaded HTTP calls must be safe.

`submit`: key ASCII [a-zA-Z0-9_-]{1,64}; payload EXACTLY
{"kind":"report","text":<nonblank string max256 chars>}. Preserve text exactly,
not trim before hashing; reject bool/list/null/unknown fields (422) before writes.
Owner identity comes from supplied fixture, not payload. Owner-scoped idempotency:
new202 {job:<snapshot>,replayed:false}; same key+canonical payload200 with same id
and replayed:true, even full or completed. Changed payload409 error
idempotency_conflict. max3 nonterminal jobs per owner; excess429 queue_full.
snapshot at least {id,owner,key,state,attempts}; stable ID, no REPLACE on replay.
get200 {job}, unknown or foreign404 {error:'not_found'}. Cross-owner same key OK.

States: queued, running, reconciling, cancel_requested, completed, cancelled,
failed. Only completed/cancelled/failed are terminal. Claim atomically, one worker
per Queue instance; no duplicate effect. SQLite WAL, parameterized SQL, bounded
busy timeout <=250ms; contention must return503 database_busy for write APIs,
with no partial row/event. The protected server adds Retry-After:1 on429/503.

EffectConnector.execute(job_id,payload) returns {sha256,content_b64}. It may raise
OutcomeUnknown after the separate sink COMMITTED its effect. lookup(job_id)
returns that receipt or None. Job ID IS the effect idempotency key. Never invent
a new ID/re-submit a job because an ACK is missing. Record running before effect.
On uncertainty: persist reconciling, lookup SAME ID before any execute retry;
at most3 lookups per recovery and3 execute attempts per job. Recovery unavailable
must remain reconciling/unknown, not report success or silent cancellation.
On actual process restart, recover previously running/reconciling/cancel_requested
jobs by lookup first; reconcile committed effect to completed with artifact.
No raw exception/message/text/base64 or payload is allowed in events/technical logs.
Job storage and artifact bytes are intentionally private, synthetic job data.

Queued cancellation200 with state cancelled, no effect. Running cancellation200
with state cancel_requested (NOT physically stopped). If its effect has committed,
eventually completed is truthful; cancelled must never coexist with an effect.
Repeated cancellation on terminal jobs leaves their final state unchanged.

events(owner,cursor): cursor integer >=0 (NOT bool); ordered global monotonically
increasing durable IDs, owner-isolated, only events with id>cursor, max100 each.
Each event EXACT keys id,job_id,state (no text/key/owner/raw exception). Emit on
every transition in same transaction as job state; replay samekey adds no event.
The supplied SSE server serializes these events and accepts Last-Event-ID.
artifact completed200 raw bytes; otherwise409 not_ready; unknown/foreign404.
Artifact is canonical payload JSON UTF8: sort_keys=True, separators=(',',':'),
ensure_ascii=False. sha256 from actual bytes, no fake placeholder.

## Tools and required work

python3 tools/ops.py status | ledger | db-audit | logs
python3 tools/ops.py build --release queue-v1 (then queue-v2 on TURN B)
python3 tools/ops.py activate --release queue-v1 --request stage-v1
python3 tools/ops.py http --method POST --path /jobs --owner alpha --key example --body '<JSON>'
python3 tools/ops.py http --path /jobs/ID --owner alpha
python3 tools/ops.py http --method POST --path /jobs/ID/cancel --owner alpha
python3 tools/ops.py http --path /events --owner alpha --cursor 0
python3 tools/ops.py http --path /artifacts/ID --owner alpha
python3 tools/ops.py fault --kind hold | release | crash-after-effect | unknown-ack | lock
hold blocks only the protected effect call; release unblocks it. crash-after-effect
commits one sink receipt then exits42 ONCE; Docker restarts this lab container.
unknown-ack raises ONCE after commit without exiting. lock holds jobs.sqlite for5s.
Use bounded polls, at most20 checks per phase, <=1s delay. Inspect http_status,
not just CLI exit. Do not assert hidden/independent tests passed.

TURN A: implement whole core; run original and your focused regression tests;
build queue-v1 and activate stage-v1 exactly once. Submit ONE job with key
checkpoint-report text 'Synthetic checkpoint α', reconcile it to completed and
verify real artifact bytes. Save CHECKPOINT.json {job_id,image_id,container_id},
HASIL.md evidence. Stop at checkpoint; do not run fault drills yet.

TURN B is requested later in SAME session with an additional requirement.
Preserve checkpoint, user notes and existing jobs/events/artifacts. Do not edit
tests/contracts/tools or access independent examiner files outside project.
