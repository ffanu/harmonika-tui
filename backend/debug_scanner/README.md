# Harmonika debug and security scanner

This package implements the supplied1342-line blueprint as an additive backend
engine. Exact numbered catalog:290 IDs in25 layers, despite the245 header. A row
is not an implementation. `catalog` exposes exact availability and execution mode.

Implemented entrypoints are explicitly scoped:

- Classification hints for supplied URL/file/network identifiers; no device discovery.
- Operator-registered target scope checks and one bounded GET, header/cookie attribute
  observations and reachability. No arbitrary URL, credentials, redirect, port scan,
  login attempt, payload fuzzing or exploitation.
- Python AST and conservative JS/TS lexical source diagnostics, exact rule evidence,
  local assignment SQL construction candidates, shell/eval/weak-hash review,
  syntax/lint, cross-reference, complexity and dependency declaration metadata.
- Secret candidates emit no value or source excerpts. Private paths are rejected;
  colliding basenames are hashed so distinct source findings are not discarded.
- Supplied logs/stack/sanitizer reports, typed browser observations, sample timing,
  evidence hash/timeline, cause graph coverage and explicit incomplete RCA.
- Redacted Markdown/real portable PDF reports and a supplied-findings CI gate.
- Eight explicit logic-model/trace validators: state transitions, workflow dependencies,
  reference consistency, integer minor-unit arithmetic, supplied-case comparison,
  top-level API DTO types and declared CAS transaction histories. No business code runs.
- Python-only intra-function taint, structural CFG summaries, constant/unreachable
  code and normalized AST clones; no whole-program or symbolic safety proof.
- JWT structure/policy/time checks (no signature verification), declared CORS policy,
  opaque sRGB contrast and selected static HTML name/alt/lang checks; no WCAG certificate.
- `scan` composes those enabled analyses. It never means a full security assessment,
  independently proven root cause, completed remediation or compliance certification.

Unsupported categories stay unavailable: full SAST/taint/symbolic proof, active DAST,
APKs/IPA/device profilers, host process attachment, memory/core dumps, network capture,
IoT credential checks, CVE feed matching, container/IaC tooling, fix/deploy/rollback,
continuous monitoring, CI connectors and legal/regulatory certification.

## API

Existing licensed endpoint: `POST https://chat.harmonika.id/v1/skills/invoke`.
No separate token/provider or new native MCP tool is needed.

```json
{"skill_id":"debug_scan.scan","input":{"data":{"files":[{"path":"main.py","content":"cursor.execute(f\"SELECT * FROM items WHERE id={uid}\")"}],"report_format":"pdf"}}}
```

`debug_scan.catalog` takes `{}`. Other skills take `input.data` with their precise
operation payload. Use `harmonika catalog invoke debug_scan.catalog` or the generic
MCP skill tool. Native guide `harmonika-debug-scanner` explains automatic routing.
The CLI helper `current/tools/scanner_cli.py <operation>` accepts inner JSON stdin,
wraps the API contract and privately saves a SHA-verified PDF in Downloads.

Do not paste source, credentials, raw JSON or base64 into customer-facing answers.
User-facing answers summarize rule IDs, redacted file references/lines, uncertainty
and scoped actions. No raw source/chat history is written to audit logs.

## Operator target registry

`/opt/harmonika-ai/debug-scanner-targets.json` is operator-owned and is never writable
through a public skill. Default `{"targets":{}}` denies all live targets.

```json
{"targets":{"approved-demo":{"url":"https://approved.example","paths":["/health"],"public_probe":true}}}
```

Only register endpoints intentionally safe for anonymous public GET probes. Private
authenticated services/customer APIs require a separate scoped connector. A request
boolean does not grant authorization. Host/scheme/port/path come from this registry;
private/link-local/multicast/unspecified DNS answers are rejected, a validated address
is pinned for the connection, TLS hostname verification is retained and redirects
are observed without following. `allow_loopback_fixture:true` is reserved for an
operator-owned literal loopback QA fixture, never a caller-selected address.

The existing isolated child runner bounds CPU/memory/wall time. Offline/invalid TLS
returns degraded with no response body, cookies or header values. These boundaries
follow the [OWASP SSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
and [Python HTTP/TLS interfaces](https://docs.python.org/3/library/http.client.html).

## Browser evidence

Existing `workflow.browser` runs supplied HTML using networkless software Chromium.
Use it for an actual screenshot hash, page error count and overflow result, then
analyze typed observations. `web.frontend.console.error` and related analysis IDs
import observations; they do not themselves launch a remote browser or infer
independent verification from a client assertion.

## Verification and activation

`python3 -m unittest discover -s tests/debug_scanner` tests each implemented contract,
positive/negative source fixtures, redaction, target authorization, offline handling,
real owned HTTP fixture, all hash-verified discovery plugins and PDF transport.
Public qualification repeats the exact active IDs through the licensed API; live
probe tests use a temporary owned loopback fixture and restore the registry afterward.

No existing ERP/CS/customer database, native binary, user process, production browser
profile, GPU/DRM setting or gateway service is changed. No Git push is performed.

## Phase 2 contract examples

Python source automatically runs the four advanced static checks in `scan`.
Non-source checks are explicit, at most four per pipeline call:

```json
{"skill_id":"debug_scan.scan","input":{"data":{"checks":[{"operation":"uiux.color.contrast","input":{"foreground":"#FFFFFF","background":"#FFFFFF","large_text":false,"enhanced":false}}],"report_format":"pdf"}}}
```

Exact DTOs are in `catalog.skills[].input_schema`. A catalog row with unavailable
status is not executable. Schema rejects unknown fields; typed JSON case values are
validated by the bounded handler because the registry intentionally has no union schemas.
Per-handler captured source contains only transitive trusted dependencies, then is
compressed with SHA/byte checks. Decode limit stays 32 MiB and registry limits stay fixed.

References: JWT assumptions follow [RFC 8725](https://www.rfc-editor.org/rfc/rfc8725.html),
CORS browser rules use [Fetch](https://fetch.spec.whatwg.org/), and contrast computation
uses [W3C contrast guidance](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum).
These limited checks do not constitute signature verification, exploit verification or
an independent rendered accessibility audit.
