# TURN B — drain, restart and uncertain outcomes

Add Queue.drain() and Queue.resume(): return (200,{draining:bool}); persist this
admission state in jobs.sqlite. While draining, new jobs503 draining, but existing
idempotent replay200, changed key payload409, queued jobs still run, status/events
and artifacts work. Restart must preserve draining until explicit resume().
The provided POST /control/drain and /control/resume call these methods; these
are lab fixture controls, not public authentication design.

Preserve CHECKPOINT.json and USER_NOTES.md exactly. Build queue-v2; activate
stage-v2 exactly once (same private volume). Verify old checkpoint job/artifact
and replay. Demonstrate each drill through the actual staging, not mock output:
1. hold; submit crash-report; await running; arm crash-after-effect; release;
   verify exit42/restart_count and same job completed via lookup, ONE effect.
2. unknown-ack; submit unknown-report; recover same job to completed.
3. hold; submit running-cancel; await running; request cancel => cancel_requested;
   submit queued-cancel then cancel it => cancelled; release; running job completes.
4. hold; submit three jobs for alpha including running, fourth rejected429;
   beta can enqueue with the SAME key as alpha; release; let jobs finish.
5. lock; attempt one new submit =>503 database_busy; after lock releases retry
   same key, reconcile same job, no partial duplicate or phantom event.
6. Read SSE events; save received last id, reconnect with that cursor; no replay
   of already received events, no foreign-owner data.
7. drain; check new503, checkpoint replay200; call tools/ops.py restart (scoped
   app restart only); admission remains draining; resume; new job accepted.

Perform local tests before immutable build. If a real bug requires code changes,
use new release queue-v2-r1/r2/r3 and activation key stage-v2-r1 etc. Never overwrite
an immutable release. Write RESULT.json {scope:'isolated_linux_docker',
production_changed:false,same_session_resume:true,completed:true} only when all
native drills and actual evidence support it. Write actual evidence and limits
in HASIL.md. Do not invent an overall universal 'Level 6 certification'.
