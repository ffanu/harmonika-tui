---
name: harmonika-debug-scanner
description: Debug and security review of supplied source/evidence, operator-approved GET probes, grounded RCA and redacted reports through the Harmonika backend.
---

Use this for a requested code/security audit, crash-log analysis, or scanner report.
Inspect `debug_scan.catalog` through the existing skill gateway first. The290 source
catalog IDs are not290 implementations. Respect exact readiness and execution_mode.

Call backend skills via the existing generic skill tool:
`harmonika catalog invoke debug_scan.<operation>` with JSON `{"data":{...}}` on stdin.
`debug_scan.catalog` takes `{}`. The bounded pipeline is `debug_scan.scan`.

For automatic PDF saving use:
`python3 -I ~/.config/harmonika-terminal/current/tools/scanner_cli.py scan`
with JSON stdin, e.g. `{ "files":[{"path":"app.py","content":"..."}], "report_format":"pdf" }`.
This stores a private SHA-verified PDF under Downloads/harmonika-ai/pdf and removes
base64 from output. Never paste base64 or raw JSON evidence as the user-facing answer.
Summarize findings with rule ID, redacted file reference, line, certainty and scope.

- Only source files/logs explicitly supplied/authorized for this audit. Never add
  unrelated customer chat history, credentials, private configs or host secret paths.
- Static findings are candidates. Do not describe SQL injection/XSS rules as confirmed
  exploits or report a clean full OWASP/compliance assessment from partial coverage.
- Live probes take operator-registered target_id/path only. A user boolean, arbitrary
  URL or screenshot is not server authorization. No redirects, credentials or active
  exploitation. Missing target registration remains denied, offline remains degraded.
- For real frontend evidence, use existing `workflow.browser` on supplied HTML in its
  networkless software-Chromium sandbox. Then pass typed observations if appropriate.
  Observation-analysis skills do not launch browsers, devices or profilers themselves.
- RCA requires supplied evidence IDs/hashes and cause relationships; independent method
  labels alone do not prove a root cause. Never generate a confident causal story or
  promise a fix/deployment that did not run. Request the missing specific evidence.
- No auto-fix, production deployment, external notifications, default-credential probes,
  user process attachment, packet capture, GPU enablement or24h monitoring is performed.
  Scoped toolchain/approval is needed for those unavailable catalog operations.

## Additional bounded checks

- Python-only `sast.data.flow`, `sast.control.flow`, `sast.dead.code`,
  `sast.duplication` accept supplied `files`. `scan` includes these automatically.
  Taint is intra-function and conservative, CFG structural, clones normalize names
  while preserving literals. Unsupported languages remain needs_toolchain.
- Eight `logic.*` handlers validate explicit state/workflow models, consistency,
  integer minor-unit calculations, supplied expected/observed cases, top-level
  API contracts and declared CAS transaction traces. They do not run business code.
- JWT/CORS inspect supplied configuration only; JWT signatures are NOT verified.
  Accessibility checks static HTML only; contrast uses opaque #RRGGBB colors and
  explicit large_text/enhanced flags. Never report full WCAG conformance.
- For non-source checks, supply up to four explicit `checks` to `scan`, each
  `{"operation":"uiux.color.contrast","input":{"foreground":"#FFFFFF","background":"#FFFFFF","large_text":false,"enhanced":false}}`.
  Inspect catalog input_schema for the exact DTO. No arbitrary operation execution.
