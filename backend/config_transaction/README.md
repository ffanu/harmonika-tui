# Configuration transaction engine

The Linux coordinator validates a complete configuration plan, runs steps through
the caller's existing registered SSH connector, checks explicit postconditions and
can compensate known completed failures in reverse order. The terminal adapter
does not invent or broaden device credentials. The worker is an owner-only SSH
stdio process, not a public HTTP listener or persistent daemon.

`network_config` now defaults to preview. Applying a user-authorized change requires
explicit `dry_run:false`. Legacy simple command strings are split only at top-level
separators: `printf '%s' 'ready;safe'` remains one command. Comments, substitutions,
compound scripts and heredocs require an explicit array of complete command strings.
An array element is opaque; its exit status describes that element, not every
internal shell statement. Prefer separate steps for checks that must be independent.

```json
{
  "target": "registered-alias",
  "dry_run": true,
  "config": [
    {
      "command": "apply-the-authorized-change",
      "verify": "check-the-required-postcondition",
      "rollback": "restore-the-reviewed-backup",
      "rollback_verify": "check-the-original-state"
    }
  ]
}
```

These are placeholders, not runnable installation commands. The operator/model
must prepare a scoped backup and meaningful checks for the actual target. This
engine does not create snapshots, infer safe rollback commands or authorize
production changes. If any compensation is supplied, every step must provide both
rollback and rollback_verify before any execution begins.

| State | Meaning |
|---|---|
| preview | No device command executed |
| verified | All supplied commands/checks succeeded; check quality remains caller-defined |
| applied_unverified | Commands succeeded, but at least one step had no postcondition |
| failed_partial | A known completed step failed and no complete compensation was supplied |
| rolled_back | Requested change failed; supplied compensation/checks restored the declared state |
| rollback_failed | Compensation or its check failed; restoration is not claimed |
| needs_reconciliation | Execution/transport/deadline was uncertain; no automatic retry/compensation |

`rolled_back` intentionally has `ok:false`: the requested configuration did not
succeed. Even a nonzero command can have side effects, so its compensation is
included. An uncertain command may still be running and must not be blindly undone
or retried. A truncated verification result is incomplete evidence.

The caller accepts only plan-matching, once-per-phase requests and rejects
unrequested commands or success claims without its own execution evidence. This
prevents duplicate requests inside one transaction; it is not cross-request
exactly-once delivery or a persistent idempotency guarantee. A nonblocking endpoint
lock on Linux serializes concurrent transactions through aliases sharing the same configured host/port.

Deployment: `/opt/harmonika-ai/config-transaction/{engine.py,worker.py}`. The MCP
adapter uses the existing private SSH alias `vps`; connector credentials remain on
the operator's machine. No AI/CS/billing service restart is required. The mechanical
SQLite WAL journal under `/var/lib/harmonika-config-transactions` stores only
timestamp, skill_id, error_type/status and latency; commands, output, target identity,
credentials and conversations are not persisted there. Private files use 0700/0600.

Run focused tests from the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_config_transaction*.py' -q
```

The owner live exam uses an explicitly registered, pinned-key loopback SSH fixture
that accepts only fixed synthetic verbs, never an OS shell. It demonstrates failed
beta verification, skipped gamma and reverse verified rollback. Paramiko fixture
interface reference: https://docs.paramiko.org/en/stable/api/server.html
