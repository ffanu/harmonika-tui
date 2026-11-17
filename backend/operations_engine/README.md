# Harmonika operations engine

Private Linux CPU service behind the existing authenticated `/v1/skills/invoke`
gateway. The Windows RTX worker remains dedicated to media. TUI, web and API use
one backend contract; no new frontend menu or provider is introduced.

Eight areas: registered health/RTX diagnosis, real isolated selftests, durable
owned job observation/reconciliation, Semgrep/Trivy scanning, Playwright static
browser assertions/traces, read-only SQLite/PostgreSQL audits, reviewed local
release deployment/rollback, and own/admin aggregate license usage.

Public IDs use `operations.*` so existing builder/core IDs keep their behavior.
Inspect exact inputs through `harmonika ops catalog` or the master skill catalog.
The `engine_operations` MCP tool exposes these operations without registering a
large separate set of model function definitions. Existing installations need the
new signed release; no unattended updater or user process termination is used.

```sh
printf '{}' | harmonika ops invoke engine.diagnose
printf '{"suite":"core"}' | harmonika ops invoke skills.selftest
printf '{"scope":"self","days":7}' | harmonika ops invoke license.usage
printf '{"project":"qualification","scanner":"semgrep","path":"."}' | harmonika ops invoke security.scan
printf '{"project":"qualification","database":"fixture","query":"by_id"}' | harmonika ops invoke database.audit
harmonika ops download --project qualification --artifact-id browser_ID/trace.zip --output ./trace.zip
```

The example qualification alias belongs to the operator. It is not automatically
shared with other API keys. Register each real project and grant its alias in the
private operator configuration. Never send a server path, principal, credential
file or arbitrary executable from a caller. Database aliases are project-local.
Unknown users cannot inherit global database or deployment connectors.

## Authentication and operation boundaries

Existing gateway token validity, device binding, scopes and quota remain in force.
The master forwards a trusted `api-token:N` principal with an independent private
transport token. Unknown caller principals are denied unless gateway-principal
mode is explicitly enabled; that mode grants only health/selftest and own license
usage, with no project or deployment permissions. All-license aggregates require
an explicit administrator grant. The PostgreSQL service role has SELECT on only
license status/time/device ID columns, not key hashes, names, contacts or chat.

`job.resume` and public `job.reconcile` observe the original durable job. Caller
facts cannot certify unknown effects. Deployment reconciliation independently
checks the registered pointer/hash and actual HTTP health. No lost ACK, expired
lease, timeout or new idempotency key authorizes a repeated mutation.

Deployment is the bounded local **release-tree** adapter in JOBS-DEPLOYMENT.md.
It does not build arbitrary programs, execute scripts, restart application
services or migrate databases. Existing deployment adapters remain intact.
No production application is switched during qualification.

Scanner adapters inspect bounded source snapshots and pinned local rules/DBs.
They do not certify an application free of vulnerabilities. Browser QA executes
static snapshots in sandboxed Chromium with typed assertions; it does not inherit
customer browser sessions or launch package scripts/application servers.
See README-adapters.md for exact capabilities and coverage limits.

Core selftest uses real isolated SQLite/job checks; it does not certify every
catalog skill. Missing tools, zero checks, partial output, wrong project scope,
failed health or rollback are not successful task completion.

## Runtime and privacy

`python3 -m operations_engine.service --config /etc/harmonika-operations/config.json`
listens only on `127.0.0.1:18280`. Config, transport/state keys and database service
files are private operator files, never package contents or process arguments.
Run as the dedicated unprivileged `harmonika_ops` service user. Source, executables
and browser binaries are root-owned; project/state/artifact directories are scoped.

The master operations budget is 45 seconds, private transport 42 seconds and
public gateway 50 seconds. Scanner/browser child execution is bounded to 25
seconds, with bounded version checks/snapshot/output. Exceeding the limit is an
explicit error, not background success or automatic retry. Other master adapters
keep their existing deadlines and slot ownership behavior.

SQLite WAL stores fixed mechanical statuses/counters/latency and HMAC scope/job
references. It does not store chat, source, SQL text, raw commands, credentials or
scanner stdout. Browser screenshot/trace artifacts are private and expire after
24 hours; a bounded pruner runs every five minutes. Temporary raw tracing data
are kept separately and removed after the owned worker closes. Download verifies
chunk and full-file SHA-256 and never prints base64; failed new partial files are
removed, existing user files preserved. Traces must not be uploaded to public
viewers without the owner's separate decision.

Trivy DB refresh is a dedicated hourly task; missing/expired DB fails closed.
On Ubuntu user-namespace restrictions, allow only the root-owned registered
Chromium executables with a targeted AppArmor profile. Keep global restrictions
and Chromium's sandbox enabled. References: [Ubuntu release notes](https://documentation.ubuntu.com/release-notes/24.04/)
and [Chromium guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md).

## Verification

```sh
python3 -m unittest discover -s tests/operations_engine -v
python3 -m unittest discover -s tests -p test_operations_client.py -v
python3 -m unittest discover -s tests -p test_operations_master_deadline.py -v
node --test tests/operations-tools.test.cjs tests/operations-receipts.test.cjs
```

Use only owned projects, child processes and loopback fixture servers for tests.
Private activation requires verified source hashes, backup/CAS rollback, a ready
new service and an idle, explicitly authorized restart of loaded master/planner
modules. Preserve user sessions and unrelated service PIDs.
