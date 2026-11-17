# Durable jobs and reviewed local deployment

These modules are an internal backend adapter. The service must authenticate the
principal, enforce registered project grants and operator review, and expose only
its explicit operations routes. They do not execute commands, restart services,
send signals, modify operator configuration or deploy to arbitrary destinations.

`JobStore(root, key, clock=time.time)` requires a private absolute state directory
and at least 32 key bytes (a 64-character hex key is accepted). Keep the key in the
service's private configuration. The store creates `jobs.sqlite` with WAL,
FULL synchronization and atomic actor/project-scoped transitions.

- `prepare(actor, project, kind, idempotency_key, request_digest, evidence=None)`
  returns the original job for matching requests. A replacement idempotency key
  does not replay the same prepared/running/unknown request. Request digests are
  server-computed SHA-256 over mechanical inputs, never chat text.
- `get/resume(actor, project, job_ref)` reads the original job; expired running
  leases become `unknown`, with `retry_safe=false`.
- `transition(actor, project, job_ref, expected_version, state, evidence=None,
  lease_owner=None, lease_seconds=120)` uses CAS and an internal worker lease.
  Terminal jobs and unknown outcomes cannot transition back to running.
- `reconcile(actor, project, job_ref, expected_version, observed_state, evidence)`
  is for trusted server observations. **Never pass caller-supplied evidence to
  it.** Deployment kinds reject this generic route and require their engine.

Only enumerated mechanics, booleans, counts and digests enter evidence. Raw
actor/project IDs, idempotency keys, lease owners and release paths are HMACed;
chat, commands, file contents, URLs, credentials and parser exception text are
not stored. Caller possession of a job reference alone is not authorization.

## Local deployment registration

Create `DeploymentEngine(root, key, config, job_store=shared_store)`. The root
service can share that store with its ownership-checked job status endpoint.
The trusted configuration is shaped as follows; the values below are examples:

```json
{
  "deployments": {
    "site-staging": {
      "release_root": "/srv/registered-site",
      "artifacts_root": "/srv/approved-artifacts",
      "health_url": "http://127.0.0.1:18080/health",
      "health_expect": {"status": 200, "body_sha256": "<sha256 of expected health body>"},
      "actors": ["api-token:7"],
      "projects": ["registered-project"]
    }
  }
}
```

The roots must be distinct, canonical, non-world/group-writable directories with
no symlink/reparse ancestors. `release_root/releases/` must already exist and
`current` must be a relative symlink such as `releases/baseline` to a healthy,
registered baseline. This adapter deliberately requires a baseline so rollback
has an identifiable previous release. It manages regular bounded artifact trees,
not archives, package scripts, services or databases. Artifacts are at most 4,096
files / 256 MiB; symlinks, hardlinks, traversal and hidden names are rejected.

1. `preview(actor, project, alias, artifact, expected_artifact_sha256=None)`
   computes a stable `plan_sha256` binding artifact digest, previous release,
   alias/configuration and resource identity. It is read-only and returns
   `mutation_authorized=false`; do not interpret the DTO as approval.
2. `apply(actor, project, alias, artifact, expected_plan_sha256, ack,
   idempotency_key)` requires literal `ack is True` from the service's reviewed
   operation. It copies to its own release directory, verifies hashes, journals
   intent, atomically switches `current`, then probes the exact registered
   loopback HTTP URL and expected body digest. It does not follow redirects,
   use proxies or contact arbitrary hosts. No shell or service manager exists.
3. Failed health triggers one guarded rollback to the original previous release,
   followed by real health and pointer/hash verification. That returns
   `ok=false, status=rolled_back, rollback_verified=true`; it is not a successful
   deployment. Unverified rollback remains unknown.
4. `resume(actor, project, alias, job_ref)` never performs an effect.
   `reconcile(actor, project, alias, job_ref, expected_version)` only observes the
   existing release and real health. It does not copy, switch, restart or replay.
5. Manual rollback uses `rollback_preview(actor, project, alias, job_ref)` then
   `rollback(actor, project, alias, job_ref, expected_plan_sha256, ack,
   idempotency_key)`. It guards the current and original previous release hashes;
   a changed/tampered backup is rejected. Rollback itself has a separate job.

Kernel advisory locks establish physical worker quiescence: lease expiry alone
cannot prove a worker stopped. A crashed worker releases its kernel lock without
signals. An unknown deployment blocks replacement requests for the same physical
registered resource even when the caller changes alias/config/idempotency key.
The local deployment adapter currently requires POSIX advisory locks; Windows
TUI clients call this backend through the service. There is no Windows server
mutation fallback.

Run the owned fixtures (no production target or user process):

```sh
python3 -m unittest discover -s tests/operations_engine -p 'test_*.py' -v
```

The fixtures include real loopback HTTP health, successful activation, failed
health with verified rollback, lost ACK observation-only reconciliation,
physical worker locks, cross-process CAS and an abrupt owned-process exit.
