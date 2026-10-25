# Engineering report contract

- **Code review:** summarize scope; cite concrete source identifier/line and
  evidence ID for findings; distinguish observations from hypotheses; explain
  severity and practical correction. Use a scope-qualified verdict. No unfounded
  approval or cross-repository security claim.
- **Debug:** describe expected/actual behavior where provided, observed errors,
  plausible causes and the diagnostics that would confirm/refute them. If the
  failing input or reproduction is missing, say so. A matching signature is not
  a confirmed root cause or a completed fix.
- **Testing strategy:** prioritize business-critical behavior, validation,
  authorization, error paths, concurrency and idempotency where relevant. Cases
  and commands are proposed; do not invent passing CI, coverage or load metrics.
- **Documentation:** use only supplied declarations, routes and configuration
  facts; label required owner input. Examples are illustrative and not executed
  unless a separate authorized tool run proves them.

`request` <=8,000 characters, `context` <=12,000, <=8 source identifiers with
<=32,000 characters each, `diff` <=64,000, `logs` <=32,000; total canonical JSON
<=128 KiB. Identifiers are relative names and do not grant filesystem access.
Automatic chat context is bounded at 240 KiB; extracted snippets are named
`snippet-N.ext` and their lines are relative to the pasted block. A PR URL alone
is not repository evidence. Public execution has no private source-control or
monitoring connector, no shell and no code execution.

Reports are read-only analysis. The local CLI returns the structured worker
result. Public chat adds model synthesis; JSON exposes `harmonika.reports[]`,
SSE exposes it on the terminal completion chunk. Missing or clipped evidence must
remain visible in the answer. No cross-customer memory or automatic training.
