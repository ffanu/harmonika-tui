# TURN B — additive migration, priority/pause and cross-service recovery
Continue the SAME native session. Preserve checkpoint, notes and old data.
Upgrade WorkflowStore schema_version=2. In one BEGIN IMMEDIATE transaction add
workflows.priority INTEGER NOT NULL DEFAULT 0, call migration_hook('after_priority_column')
if supplied, add workflows.paused INTEGER NOT NULL DEFAULT 0, set user_version=2.
Fresh schema2 creation goes through v1 then v2 in the creation transaction; the
hook runs after the priority column only when upgrading, never on already-v2 init.
Hook exception or real lock must roll back ALL schema/version/data changes. No DB
recreation. Constructor v1 may read v2 without lowering user_version. Old completed
workflows/tasks/events/artifact/idempotency stay intact.

v2 input allows optional priority (int0..9, NOT bool), default0. Canonical identity
for legacy requests is exactly the original payload: do NOT inject default priority
into payload of absent field. Explicit priority:0 is a different canonical
request from an absent priority (same-key conflict409 is intentional). Snapshot adds priority/paused. Legacy rows default0/false.
Across ready workflows, dispatch higher priority first; ties creation order then id.
Never reorder already submitted jobs. pause(owner,id)/resume(owner,id) =>200 snapshot
and owner-isolated404; durable paused flag/event. Pausing blocks NEW reservations;
in-flight/unknown children must keep being reconciled. Resume uses existing child
keys. A paused workflow may complete if all already-dispatched children complete.
Parent pause/resume event uses task=null and event state paused/resumed; only a
flag change adds this event. Control on terminal workflow returns200 unchanged.
Use POST /workflows/ID/pause and /workflows/ID/resume. No inference that pause/cancel
kills physical work. Live startup migration busy may fail boundedly and be retried
by the owned container policy; never set a success version before commit.

Run regression tests, build/deploy workflow-v2 once, then all drills. Every fault
receipt is scoped to LAB_RELEASE set by the protected controller. Activation clears
only the four armed child-fault markers; all previous spend/target/audit evidence
remains intact. Re-arm the named faults for each tested corrected v2-rN release.
Use workflow request keys <release>-ack, <release>-crash, <release>-bad-artifact,
<release>-offline, <release>-pause, <release>-cancel-queued,
<release>-cancel-running, <release>-priority-low, <release>-priority-high.
A corrected image must run fresh final-release drills with fresh keys, while
reconciling or honestly cancelling old failed cases and preserving their data.
Do not reset prior fault receipts or reuse a request key for a different input.

1. Arm child-submit-ack-loss and child-lookup-fail-two, submit a2-step workflow once.
   It must recover through the SAME request identity after2 target-bound503 lookups;
   exactly one child POST/effect per step. Verify child/parent bytes and audit calls.
2. Arm workflow-crash-after-submit, submit once, verify real coordinator exit42,
   restart, same child identity recovered before checkpoint, no duplicate effects.
   Read events immediately; controller archives observed Docker events durably.
3. bad-child-artifact: corrupt one actual child artifact response; parent must
   re-read it, never accept corrupt bytes as completed or submit a replacement.
4. Sink offline: parent remains running/reconciling/cancel_requested; degraded describes readiness503,
   not an extra workflow state. /healthz remains200 while /readyz is503; recover same workflow when
   sink returns. Prove liveness vs readiness and no new workflow POST.
5. Pause a workflow while its root child is held, release children, show child
   reconciliation continues while descendants remain unsubmitted. Restart the
   coordinator with pause persisted; resume existing workflow and verify completion.
6. Cancel before any dispatch (no children), and while a child is physically held.
   Reconcile terminal children; no descendants submitted, no parent artifact.
7. Run tools/ops.py migration-drill: YOUR v2 implementation on immutable actual v1
   snapshot, injected post-ALTER failure, real writer lock, retry and repeated init.
   The clone never starts a coordinator; original live DB remains authoritative.
8. Priority drill: stop coordinator, queue priority0 and9 workflows using the release-prefixed priority-low and priority-high request keys above; start and
   verify the first newly submitted child belongs to priority9, then both complete.

After these drills settle, stop the coordinator and run workflow-burst AGAIN on
the final v2 release. Labels/cache are release-bound; this must be a new60-request
proof for this exact image, with separately reported fill/quota probes. Start the
coordinator and verify all newly accepted workflows/children/artifacts settle.
Do not reuse the v1 load proof as evidence for v2 concurrency.

After all work settles, CODE rollback to the passed TURN A release named in UPGRADE-BASELINE.json, request rollback-v1;
verify readiness and exact old checkpoint/artifact on current schema2, then restore
tested workflow-v2 request restore-v2. Never roll back DB/sink/child data. Corrected
v2-rN after those receipts uses fresh rollback-v1-rN/restore-v2-rN and must repeat
these checks. Final image equals tested corrected source; no mixed-version live
migration claim from this settled rollback.

Update HASIL.md retaining failures/repairs and RESULT.json {scope:'isolated_durable_dag',
production_changed:false,same_session_resume:true,completed:true}. No external
provider/production/RTX claim. Preserve original assertions and fault receipts.

CHECKPOINT.json remains the exact original TURN A record after all upgrades.
