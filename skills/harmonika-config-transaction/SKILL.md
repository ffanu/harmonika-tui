---
name: harmonika-config-transaction
description: Preview, execute and verify explicitly authorized registered-device configuration plans with bounded Linux coordination and declared reverse compensation.
---

Use actual tool discovery for network_config and network_targets. The coordinator runs on the Linux backend via private stdio; the terminal executes only the plan-bound requests through its registered SSH connector. Do not invent a public deployment API or transfer private keys to the backend.

Preview is now the default. For a specific change already authorized by the user, explicit dry_run=false applies the reviewed plan; do not ask again for permission that is already present. Audit-only tasks remain read-only. Use an explicit config array for scripts; simple legacy strings preserve semicolons inside quotes. Never manually split shell scripts by every semicolon.

A config array may contain complete command strings or step objects with command and optional verify. If compensation is requested, EVERY step must have rollback and rollback_verify before execution. Prepare a scoped backup and meaningful postconditions; the engine does not invent snapshots or restore commands. Keep device-specific command syntax and root scope limited to the user's task. No protected process/service may be stopped to make tests pass.

Failed known-completed commands/checks stop dependent work and may run supplied compensation in reverse, including a failed command that could have partial side effects. Timeout/unknown/disconnected results require reconciliation; never retry mutations or compensate while a command might still run. The adapter rejects unplanned or duplicate in-run commands; this is not cross-request exactly-once execution.

Interpret state explicitly: preview=no execution; verified=all supplied checks succeeded; applied_unverified lacks at least one postcondition; rolled_back means the requested change FAILED and declared restoration was verified; rollback_failed needs investigation. Never call a rolled-back release a successful activation or infer application health from a generic true check. Truncated verification is incomplete evidence. Backend target_busy means no second transaction was started.

Report concrete step/check/compensation outcomes and what remains uncertain. User data, credentials, commands and raw output do not belong in system logs/shared memory. The new SQLite journal stores only mechanical timestamp/skill/status/latency. All existing connector permissions remain in effect.
