# TURN B — stale live workers, connection loss, real stream and snapshot

Continue the SAME native session. Preserve CHECKPOINT.json, notes and prior data.
Implement stream_reader.py/storage_tools.py, regressions, build cluster-v2 then
activate stage-v2 once. Existing jobs/events/receipt hashes must remain intact.

1. Stale live worker: stop worker2/worker3, hold-worker worker1. Submit stale-report,
   await running worker1/fence. Start worker2; after lease expires verify a higher
   fence completes SAME job. Release worker1; its late execute must be fenced out
   and cannot change completion/artifact. Read sink calls, job events and states;
   prove the old container stayed running. Start worker3 afterwards.
2. unknown-ack + lookup-fail-two: submit one job, reconcile committed effect using
   same ID after2 lookup503 responses, no new submit/duplicate effect. Then test
   crash-after-effect and actual worker exit42/automatic restart/reconciliation.
3. bad-receipt: a corrupted receipt must not be delivered as a valid artifact;
   reconcile correct receipt from sink, no re-execution if already committed.
4. stop sink, submit offline-report; observe honest nonterminal state, not success.
   Start sink, reconcile SAME job to completed without a new POST. Retain evidence.
5. drop-next-submit then POST lost-submit; recover via GET /requests/lost-submit
   and job/artifact, without another POST. The bridge returns exit75 deliberately.
6. Run tools/ops.py stream. It imports YOUR EventReader in the deployed image:
   opens real continuous chunked SSE before creating jobs, observes later events,
   forces a midstream disconnect, reconnects with accepted cursor, checks beta
   isolation and hashes stream-client file. HTTP200/EOF alone is not passing.
7. Run tools/ops.py backup-drill. It runs YOUR backup/restore on live WAL while
   submitting work, restores a separate recovery clone, and tests corrupt input,
   bad checksum, symlink refusal and unchanged existing target. Original cluster
   stays authoritative; do not restore an older queue over newer external effects.

After all accepted jobs settle, test CODE rollback using the already built
cluster-v1 with request rollback-v1; check readiness and old checkpoint/artifact.
Then restore the tested current cluster-v2 (or corrected v2-rN) using request
restore-v2 and verify it again. Never restore DB/sink or rebuild the old release;
keep all receipt IDs and hashes. These two request keys are explicitly allowed
by the typed controller. For a corrected v2-rN after this rollback/restore has
already happened, use fresh rollback-v1-rN and restore-v2-rN request keys and
repeat readiness/checkpoint/artifact checks. Never replay an old activation key
with a different release. Final active image must be your tested v2 revision.

EventReader(allowed_job_ids=None,last_event_id=0,max_frame_bytes=16384): feed(bytes)
returns list of accepted {id,job_id,state}. Arbitrary byte chunks including split
UTF8/CRLF, multiline data and heartbeat/comments; dispatch ONLY at blank frame end.
last_event_id advances only on accepted complete matching job event; ignore
duplicate/stale/foreign job events. Validate numeric IDs (not bool), wire id equals
JSON id, required keys and string state/job_id; malformed frame or overflow raises
ValueError without advancing cursor. Preserve partial bytes until more arrive;
no partial-frame delivery. finish() rejects incomplete UTF8/nonempty partial frame.
Default SSE message (no event field, or explicit event: message) is a known job
message and must validate the same schema/ID rules. Only explicit unknown SSE
event types can be ignored without cursor advancement. Job state
must be one of queued/running/reconciling/cancel_requested/completed/cancelled/
failed. Oversized comment frames also raise ValueError; do not retain a seen-ID
set. Do not resubmit
mutations in a stream reconnect/parser. Keep buffers bounded even on comments.

storage_tools.backup(source,destination) returns manifest {sha256,bytes,integrity,
job_count,event_count,last_event_id}. Use SQLite online backup API while writers
run; source journal remains WAL. Closed standalone snapshot uses DELETE mode,
integrity ok,0600 from creation, no sidecars, atomic new destination; no overwrite
or symlinks, including a destination appearing during publication. Compute the
manifest from the closed snapshot, not live source. Publish without replacement
(for example same-directory temp+link), fsync file/directory and clean temporary
files on failure. The recovery clone must NEVER be started as an active queue.
restore(source,destination,expected_sha256) validates hash, SQLite integrity,
refuses symlink/existing destination and publishes a new0600 standalone clone
atomically. Restored standalone bytes must match the verified snapshot exactly.
Wrong hash/corrupt input must leave destination absent/unchanged.
Backup clone is point-in-time queue data, not a multi-database disaster recovery
or proof all external effects roll back. Report that scope accurately.

Reconcile all accepted jobs before completion, resume admission if drained,
verify health/all3 workers and checkpoint. Write RESULT.json with scope
'isolated_linux_multiworker',production_changed=false,same_session_resume=true,
completed=true; HASIL.md records actual evidence, failures/repairs and limits.
If a real bug needs source correction, publish a new immutable revision and
retest it; preserve original failures. Do not reset protected fault receipts,
alter assertions, claim independent tests or touch production/RTX.
