# Zimbra recovery assessment

Version 1.4.3 includes twenty-four reviewed Q&A lessons in `knowledge.py`.
`zimbra_qa(topic)` returns an index or one lesson without contacting a server.
`zimbra_health` attaches matching `recovery_guidance` in prerequisite-first order.
These rules improve the agent's decisions; they are not model training, a store of
user chat, or automatic authorization to change a server. Process-control approval
is scoped to the target/component and action actually authorized by the user.

## Scoped SSL automation

`zimbra_ssl_plan(target, cert_name, hostname)` collects a read-only renewal plan.
The fixed probe parses native Certbot ConfigObj format, inventories hook hashes,
and tests the installed CLI parser against synthetic `/bin/false` hooks without
running them. It proves both config-hook override and directory-hook isolation.
Unknown remote fields are rejected before an agent sees the payload.

`ssl_worker.py` is a standalone Linux root worker for the verified standalone
Certbot/Zimbra installation. Install source as
`/usr/local/lib/harmonika/zimbra_ssl_worker.py` (root-owned 0644). Its exact config
is root-owned 0600 under a root-owned, non-writable parent:

```json
{"cert_name":"mail.example.test","hostname":"mail.example.test","service_control_approved":false}
```

Modes are `--inspect`, `--dry-run`, `--renew`, with `--config PATH`. Inspection and
unchanged, not-due checks do not stop/start services. The worker uses a per-lineage
flock and root-private state at `/var/lib/harmonika/zimbra-ssl/<cert_name>/`.
SQLite events have exactly timestamp, skill_id, error_type and latency fields,
bounded to 1,000 events. They contain no commands, output, credentials or chats.
Raw vendor/Certbot output is captured privately and never copied to this log.

Before staging or any due renewal/deployment the explicit service-control config
flag must be true under actual user authorization. A successful ACME staging run
creates a receipt tied to worker version, SANs and both renewal/global config hashes.
Production requires this receipt. The binding is refreshed only after verified
production deployment, allowing repeat renewals without monthly human approval.
A changed config/version requires reconciliation or a fresh staging test. Dry-run
never deploys staging certificates.
The current implementation accepts standalone, production Let's Encrypt and the
installed 30-day due policy; unsupported plugin/policy changes fail closed.

Production preserves existing SANs, verifies OS trust, hostname, at least seven
days validity and RSA/EC SPKI key pairing, stages privately for Zimbra, appends
an installed chain-compatible ISRG root, and runs `zmcertmgr verifycrt` before
changing the commercial key. Scoped root-private backups precede vendor deploy.
Official restart occurs only for a changed certificate or confirmed rollback.
All six enabled endpoints (443/25/587/465/993/7071) must present the expected
certificate; SMTP tests perform STARTTLS/EHLO without authentication or mail.
This fixed deployment profile expects all six endpoints enabled.

A failed ACME command with a confirmed outcome restores proxy in `finally`.
An uncertain mutation leaves a persistent reconciliation marker and preserves
private deployment staging; no automatic retry or rollback is attempted until
an operator reconciles execution. A confirmed deployment failure restores the
old key and redeploys the backed-up certificate/CA through the vendor, then
verifies rollback. Backups are retained privately for operator recovery.

`renewal.timer_units(config)` renders a twice-daily, persistent systemd schedule
with randomized delay and no forced issuance. It does not install/enable units.
Replacing an existing scheduler must be reviewed to avoid duplicate jobs and
effects on other lineages. Do not disable a shared Certbot timer without checking
all its consumers. Do not activate production before maintenance permission,
staging ACME and deployment prerequisites pass. Unit tests, an inspection, or an
active timer are not proof of end-to-end renewal/deployment.

The supervised native Harmonika candidate was a mock-only skeleton. The reviewed
worker adds its missing live adapter, cryptographic validation, persistence and
recovery. Actual activation status belongs in the deployment report, not this
source capability description.

The pure `zimbra.qa` skill is also published through the existing backend discovery
gateway. Build its standalone module from the canonical knowledge source with
`python3 -m backend.zimbra.build_discovery /path/to/new-staging-directory`, review
the resulting source/hash, then install the directory atomically under the trusted
operator skills root. It requires no SSH, network, secrets or chat history. Invoke
`POST /v1/skills/invoke` with `{"skill_id":"zimbra.qa","input":{"topic":"index"}}`
and the caller's authorized Bearer token. The gateway retains auth/quota/schema
checks and mechanical SQLite auditing. Do not publish remote-control credentials
or enable SSH mutations through this pure knowledge skill.

`zimbra_tls_audit(target, mail_domain)` is a fixed read-only probe through the
registered Linux IP connector. It verifies trust, SNI hostname, validity and TLS
for HTTPS443, actual SMTP STARTTLS25/587 (including post-TLS EHLO), IMAP993,
optional implicit SMTP465 and administration HTTPS7071. It also records bounded
DNS metadata and selected clock/certbot-timer state. No AUTH/MAIL/RCPT/DATA,
mailbox, private keys, raw TXT reporting addresses, renewal or configuration writes.
SPF literal mismatch does not prove failure when a/mx/include/redirect can apply.
DMARC p=none is a policy observation, not an automatic change request; current
DMARC reference is RFC9989 (2026), which supersedes RFC7489. DNS tree walk,
SPF sender evaluation, DKIM signing, revocation, relay acceptance and delivery
remain untested. A timer proves scheduling only, not issuance or Zimbra deploy.

`required_tls_verified` covers the expected endpoints from one perspective.
`status=review_needed` can contain advisory hardening observations while the
application remains healthy. External reachability requires a separate external
observation. The Mac test client may lack TLS1.3; this is never evidence that the
server disables TLS1.3. Legacy TLS rejection is explicitly untested. SMTP25 has a
bounded twelve-second greeting timeout because four seconds produced an audit
false negative; failure reports preserve the protocol phase and technical error.

`zimbra_health(target)` is a fixed read-only probe over the existing registered
SSH connector. It checks `zmcontrol status` as zimbra, effective read/write/search
access to the log directory, verified web HTTPS, SMTP EHLO and IMAP TLS greeting.
No mailbox, account list, message body, authentication credential, private key or
raw application log is read/returned. Protocol tests never issue MAIL/RCPT/DATA.
`ok` means collection; `healthy` is a separate verdict. Healthy here does not
claim message delivery, external DNS/reputation, mailbox content or backup health.

Web verification tries `/` and then `/zimbra/`, with a bounded unauthenticated
GET and a Zimbra login marker. HTTP 200 alone is insufficient; a 404 on an unused
context must not trigger a repair of a healthy root-mounted portal. Response HTML,
cookies and nonces are never returned.

Stats verification also checks the fd wrapper/root collector PID identities,
owners and non-zombie state, plus sample-file metadata. An active orphan collector
with a missing/dead wrapper remains an explicit inconsistent state even if a
status command reports Running. No sample contents or process command lines are
returned; removing the stale receipt is not a repair.

`zimbra_component(target, component, operation, authorized_repair)` offers fixed
`status` and `start` commands for mailbox, amavis, logger and stats in the zimbra
login environment (including its Perl library path). Start requires existing user
repair authorization, skips a running component, and refuses unknown/truncated
state. Exit zero with printed failure is failure. Every start needs subsequent
`zimbra_health`; a successful controller call is not a health verdict.

`ProgressBudget` bounds consecutive tool-free agent steps in the supervised
repair runner. Its interruption means reconcile, never that a remote operation
was cancelled or is safe to repeat. It does not alter the global TUI retry loop.

The canonical pure assessment engine is installed on Linux and copied to the
owner runtime as `zimbra_engine.py`. The current owner's browser/SSH adapter
loads the MCP extension with `configure(load_targets, ssh_exec)`. Credentials stay
inside the private registered-target connector, not the skill or source package.

Service probes are compressed before shell transport because quoting expansion
can exceed the SSH command boundary even when the source itself fits. Keep the
8192-character transport cap; never lift it to disguise an oversized request.

Repairs use the existing plan-bound configuration coordinator, explicitly scoped
backups and postconditions. A permission error should lead to inspecting metadata
and the vendor rule, not recursive chmod or a whole-tree repair by default. A
listening proxy plus HTTP 502 is not a healthy mailbox. False stopped status can
come from PID/log access failure; fix prerequisites and recheck before starting
anything. Never kill unrelated processes, reboot, change SSH/firewall or stop
healthy services just to make a test pass. Long start operations need a tracked
job/receipt; unknown execution must be reconciled, not blindly repeated.

Reference sources:
- https://zimbra.github.io/documentation/zimbra-10/adminguide.html
- https://wiki.zimbra.com/wiki/Zmcontrol
- https://wiki.zimbra.com/wiki/Fix_the_Zimbra_Collaboration_Permissions

Some older wiki pages are explicitly archived. Check the installed version and
vendor script rather than copying obsolete commands or inferring exact modes
without evidence. If an exact original mode is unavailable, record that limitation
and justify a minimal, reversible access correction; do not stall indefinitely on
metadata research while the confirmed prerequisite remains broken.

## Mailjet relay and account prerequisites

`zimbra_mailjet_plan(target)` performs a non-secret read-only preflight on the
registered Linux host: available map types, existing-routing conflicts and
verified STARTTLS to in-v3.mailjet.com:587. It never reads keys, authenticates,
sends or changes mail routing. `relay.py` provides an invokable rollout contract
with persistent Zimbra10 attributes, exact bracket/port LMDB keys, a secure TLS
policy and explicit credential/sender-validation gates. A plan is not activation.

The owner-local `mailjet_credentials` Chrome tool supports sanitized account audit
and direct private capture. Diagnose verification, access, account role, quota
and UI errors before repeating a Generate action. Key generation requires an
explicit owner grant, and the account's security/email prerequisites still apply.
The tool never returns keys. No screenshot, DOM dump, cookie or account email is
added to public knowledge. Existing key reset/generation cannot be blindly retried
on unknown execution. Physical click support is limited to the exact observed
Mailjet control; it does not grant general desktop access or bypass verification.
