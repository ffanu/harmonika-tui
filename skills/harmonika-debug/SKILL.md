---
name: harmonika-debug
description: Reproduce bugs, inspect redacted diagnostics, fix the root cause and verify a focused regression.
---

Use for failures, regressions, unexpected behavior or diagnosis. Read README/instructions and inspect Git status before edits; preserve user changes. Start at the concrete symptom and likely failure points: recent diffs, configuration, logs, lint and existing tests.

Record a minimal reproduction, expected versus observed result and environment. Read relevant source with native file tools and read-only LSP; run the narrowest existing check through bash. Redact tokens, authorization headers, cookies, credentials and personal data before displaying logs. Do not expose secrets by dumping environment variables or entire config directories.

Distinguish evidence from hypotheses. Change the smallest responsible code path and add a focused regression only when it tests meaningful behavior. Reproduce again, then run relevant checks. Do not claim a root cause or successful fix without evidence. If blocked, report the exact failing command, redacted error, missing dependency and next useful step; never silently disable tests or claim they passed.
