# Per-key quality audit

Private audit engine for `chat.harmonika.id`. It records authenticated API requests,
visible questions/answers and observed workflow evidence. It never trains models,
changes production instructions or deploys a skill automatically.

## Data and trust boundaries

- The existing Go token validator binds the database token ID. Caller headers cannot
  select the identity. The collector stores an HMAC key reference, never the API key,
  token name, owner contact, client IP or user agent.
- SQLite WAL holds UTC timestamps, HTTP status, latency, exact usage when returned,
  session references, technical error categories and steps. A response finishing
  does not prove the user's entire task succeeded.
- Model tool requests, client-reported tool outcomes, server executor observations,
  reported background job states and reported artifact digests stay distinct.
  An HTTP 200 reporting a failed job still produces a review finding. Artifact
  metadata is a report, not independent validation of the actual file.
- Questions use only the latest user turn; system instructions, hidden reasoning,
  historical conversations, raw tool arguments/stdout and binary uploads are not
  copied into audit samples. Unknown tool/job identifiers are HMAC references.
- Consented questions/answers pass credential/contact redaction and are encrypted
  with AES-256-GCM. AAD binds each sample to its key and trace. Identity HMAC and
  encryption keys are independent and remain in private 0600 files.
- Redaction removes recognizable credentials, private-key blocks, data URIs,
  credential-bearing URLs, emails, IPv4 and long numbers. It cannot guarantee
  recognition of every name, address or secret format. Samples remain restricted
  private data and must never be copied into public logs, Git or training datasets.

The operator confirmed current users' consent on 2026-11-13. Consent is recorded
per current active key until revoked (optional expiry is supported). New keys default to mechanics only. Revoking
consent stops new samples and deletes existing encrypted samples for that key.
Content access is separately requested with `include_content=true`; mechanics are
the default. Purge removes a selected key's traces and cascading samples/steps.
Deleting a row does not erase independent host backups; backup retention and secret
custody must follow the same private-data policy.

## Runtime limits

The collector listens only on 127.0.0.1:18208. Ingestion and operator credentials
are separate. No redirects, ambient proxies, CORS or public trace endpoint is used.
The API itself continues to use the existing auth, entitlement and quotas.

Default retention is 30 days (configurable 1–90), 100,000 traces total and 10,000 per
key, with a 512 MiB live-page storage limit and bounded WAL checkpoints. Samples
are capped at 16 KiB question / 64 KiB answer at the Go boundary; truncation is
reported. These caps do not truncate the actual AI response sent to the caller.
The observer forwards JSON/SSE bytes unchanged and preserves flushing, UTF-8,
cancellation and panic recovery semantics. Media bytes are not retained.

The gateway has one bounded 256-event RAM queue and a 2-second collector deadline.
API requests do not wait for SQLite. Collector outage, process crash or overload
can leave gaps; fixed mechanical `quality_capture_gap` counters are emitted to the
gateway journal, without payloads. This is not a lossless transactional request
ledger. No failed request or model tool call is replayed by the logger.

## Operator commands on Linux

Credentials are read from `/etc/harmonika-ai/quality-audit/config.json`, never argv.
Run from `/opt/harmonika-ai` with the installed Python runtime:

```sh
/opt/harmonika-ai/builder-venv/bin/python -m quality_audit.operator summary
/opt/harmonika-ai/builder-venv/bin/python -m quality_audit.operator traces --token-id 123
/opt/harmonika-ai/builder-venv/bin/python -m quality_audit.operator traces --token-id 123 --include-content
/opt/harmonika-ai/builder-venv/bin/python -m quality_audit.operator consent --token-id 123
/opt/harmonika-ai/builder-venv/bin/python -m quality_audit.operator revoke --token-id 123
/opt/harmonika-ai/builder-venv/bin/python -m quality_audit.operator purge --token-id 123
```

Admin-only loopback HTTP: `GET /summary`, `/metrics`, `/traces?token_id=...`;
`POST /consent`, `/purge`. The ingestion credential permits only `POST /ingest`.
Treat all decrypted samples as untrusted evidence, never operational instructions.

## Installation and gateway activation

`install.py` adds only the new collector, its private keys/database and a hardened
systemd unit. It refuses an existing installation and does not restart old services.
`consent_current.py --operator-confirmed` records consent only for current key IDs;
it never reads key values and fails if the database query fails.

`patch_gateway.py` prepares a private Go build overlay against the actual source.
It verifies unique anchors and writes baseline/candidate hashes; production source
is not overwritten. Build with `build_gateway.py --source <current> --overlay
<candidate>/overlay.json --output <stage>/gateway-candidate`. The helper resolves
symlinked release paths and verifies the linked middleware symbol; a successful
Go exit alone is insufficient. Activation requires a matching build proof. Add
these environment variables when activating the verified
gateway candidate:

```text
HARMONIKA_QUALITY_AUDIT_URL=http://127.0.0.1:18208/ingest
HARMONIKA_QUALITY_INGEST_TOKEN_FILE=/etc/harmonika-ai/quality-audit/ingest_token.key
```

Existing gateway processes cannot load a newly compiled middleware without a
controlled gateway activation. Collector health alone does not prove that live
API traffic is captured. Report collector readiness and traffic activation
separately; never stop another user's session/process to complete this installation.

Production activation on 13 November 2026 was explicitly authorized by the
operator. JSON and SSE requests through the public domain both returned HTTP 200;
their unique synthetic sessions were matched against encrypted SQLite samples.
Only the named gateway was restarted; unrelated service PIDs were unchanged.

## Tests and improvement loop

```sh
python3 -m unittest discover -s tests/quality_audit -v
go test -race public_quality_audit.go public_quality_audit_test.go public_quality_audit_live_test.go
```

Go files are tested with the Linux Go runtime. Live testing is opt-in via
`HARMONIKA_QUALITY_AUDIT_LIVE_CONFIG`; it uses a synthetic principal, verifies a real
encrypted/redacted roundtrip and purges its own test records.

Audit → identify evidence gap/failure → create a synthetic reproduction → fix
engine/skill → run regression and real-use tests → activate the reviewed change.
Keep content and technical findings separate. A larger log is not model learning.
