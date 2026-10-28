# Debug/security scanner boundaries

- Read README.md and full specification.txt. Preserve exact source numbering/SHA.
- Code belongs in debug_scanner; do not touch ERP, CS/customer/billing DB, GPU or services.
- Never execute supplied source, shell, arbitrary host paths or arbitrary target URLs.
- HTTP probes use operator-registered aliases only, GET only, bounded/no redirects,
  pinned resolved address and TLS validation. Never infer authorization from a user
  boolean or allow untrusted target to authorize itself. Tests use owned fixtures.
- Evidence uses file/hash/line/rule and redacted summaries, no secret/source excerpts.
  Technical logs never contain source/chat history/credentials/response body.
- Candidate SAST rule matches are not confirmed exploits. Never infer CVEs from a
  version without a verified advisory feed, certify compliance, fabricate RCA or
  claim a fix/deploy/24h monitor happened. Unconfigured toolchains remain explicit.
- Catalog rows are not active implementations. Every active operation needs exact
  schema, real handler and positive/negative/discovery/public qualification.
