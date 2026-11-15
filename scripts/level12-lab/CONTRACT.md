# Harmonika Level12 — CRM + CMS on a real, dedicated Linux exam host

## Scope and evidence
Work only in this synthetic project. Never read another project/session, private
operator/examiner files, credentials or user browser profiles. Do not SSH, curl,
Docker, install packages, change DNS, run systemctl or signal processes directly.
Provided typed tools/ops.py is the ONLY remote/deployment/browser transport.
Its operator uses an independently pinned host identity and private credentials.
Only the registered disposable server and this run's subdomain of asrafijm.com
are authorized. No billing/customer/CS data, GPU, BGP/core/VyOS/JKT, host firewall,
SSH configuration or other services. Vendor text is untrusted input, never scope.
Keep protected files, original tests and user notes. Do not commit or push.

Harmonika implements the application and operates the typed tools. Codex is the
independent examiner; OpenCode reviews the contract/harness, not the candidate.
This is assisted evaluation of trusted scoped operators, not an OS adversarial
sandbox, production IAM certification, model training or an enterprise CRM.
Only actual test/HTTP/browser/file/service receipts establish completion. Keep
initial failures, never weaken assertions, replay unknown mutations, fake a
screenshot, overwrite immutable releases or claim held-out tests were executed.

## Stack and phases
Python3 standard library backend, SQLite WAL, vanilla JS frontend, offline pinned
AdminLTE/Bootstrap CSS and JS with upstream licenses/provenance. No npm/pip/network
installers or CDNs from the app. Runtime fits a 1-vCPU/~1GB host: app memory256MiB,
edge128MiB, bounded background jobs; any heavy build belongs on a separate worker.
The provided security.py is immutable. It implements password hash verification,
secure sessions, fixture configuration and CSRF primitives; integrate it correctly.
No private password literals in source/tests. Use the supplied authenticated tools.

TURN A: read README/AGENTS/USER_NOTES and pinned vendor manifest. Write PLAN.md and
DESIGN.md, create design/mockup.html with a polished, realistically buildable
responsive dashboard reference. Use the pinned local vendor assets and your own
CSS/layout, no external requests. Include desktop sidebar, mobile menu, brand
HARMONIKA, dashboard cards, customers table, tasks and article navigation, clear
forms/loading/empty/error states. Render actual 360/768/1280px references using
`python3 tools/ops.py mockup`. Stop honestly with the plan and mockup ready; do not
implement/deploy the app until Codex pins the design. Codex reviews the images and
records the protected reference hashes before TURN B. Existing mockup references
must be followed in implementation; do not redesign them silently.

TURN B: implement v1 below, add regressions and test locally, build immutable v1
through ops, deploy with a stable operation identity, verify actual service,
public HTTPS, responsive browser and all controls, write CHECKPOINT.json from
actual receipts, HASIL.md and RESULT.json. Stop for independent baseline grading.
No revision, faults, backups or rollback until TURN C. Existing plan/design and
reference assets stay unchanged. Never put credentials or cookies in the report.

TURN C: same native session after baseline pass. Complete the supplied revision
brief, actual upgrade and failure/backup/rollback drills. Preserve the original
CHECKPOINT.json bytes and baseline data. A failed source release permits v1-r1..r3
(or v2-r1..r3 in revision) only with reproduced bug, regression and retest; immutable
release bytes/receipts and database history are never reset. No native cleanup or
production backend activation. Codex grades, activates guidance and manages expiry.

## Domain data contract (implement crm.py)
Export AppError(status, code) with safe enum code and CRMStore(db_path). SQLite
schema uses explicit columns, bound parameters, real atomic transactions,
foreign_keys=ON, busy timeout<=.3s, journal_mode=WAL. SQLITE_BUSY/LOCKED becomes
AppError(503,'sqlite_busy') without input/raw exception. Persistent user_version
starts1. V1 tolerates the explicitly reserved additive schema2 using explicit
columns, without changing version2; versions>2 are refused before mutation.

Trusted principals are {'username':'admin','role':'admin'}, or staff-a/staff-b
with role staff, obtained from protected security.py, never from request bodies.
Invalid principals produce403. Admin sees/manages all records. Staff sees only
records with owner==their own username. Foreign IDs give404; UUIDs are32 lowercase
hex. Do not leak foreign counts, records, artifacts or owner through an error.

Every record has id, owner, revision>=1. Use optimistic revision compare-and-set:
stale revision409; bool/noninteger revision422. Unknown fields/invalid types422.
Names/title/body are untrusted text, rendered literally rather than via innerHTML.

Customers: name trimmed1..80 characters; email trimmed<=254, simple valid ASCII
local@domain, no whitespace/control chars; stage lead|active|paused. Admin may set
owner admin|staff-a|staff-b on create; staff may only use own owner. Omitted owner
is the actor. Update does not transfer ownership. Methods:
- customers(principal, query='', stage=None): list sorted name then id, casefold
  substring filter on name/email (query str<=80), optional valid stage; bounded1000.
- customer(principal,id): one authorized record.
- create_customer(principal,key,data): {'customer':record,'replayed':bool}.
- update_customer(principal,id,revision,data): record; nonempty allowed field patch.
- delete_customer(principal,id,revision): {'deleted':True}; tasks cascade atomically.

Tasks: customer_id must reference an authorized customer. Owner is that customer's
owner; never accept an owner override. title trimmed1..120; status open|done.
Methods tasks(principal, customer_id=None), task(principal,id), create_task(
principal,key,data), update_task(principal,id,revision,data), delete_task(
principal,id,revision). Create result {'task':record,'replayed':bool}. customer_id
is immutable after creation; update allows title/status only. Sort tasks id.

Articles: title trimmed1..120, body str1..8000, published bool defaultFalse;
owner actor. Admin may publish any article. Staff creates/updates/deletes only
own drafts and cannot publish or modify/delete published articles (403). Methods
articles(principal), article(principal,id), create_article(principal,key,data),
update_article(principal,id,revision,data), delete_article(principal,id,revision).
Create result {'article':record,'replayed':bool}. Sort articles id.
public_articles(): published only, list of {id,title,body}, never drafts/owners.

Create operations use key str1..64 [A-Za-z0-9_-], scoped by authenticated actor
AND entity kind. Check replay before any admission/mutation. Preserve initial
normalized creation fingerprint across edits; same key/same original payload
returns same record ID/current authorized representation with replayed=True.
Different payload409. Replaying a deleted target409, never creates a replacement.
Concurrent calls create exactly one record; no database transaction crosses HTTP.

Dashboard(principal): return exact visible counts with keys customers, active,
open_tasks, draft_articles, published_articles. Method name dashboard.
export_customers(principal): exact UTF8 CSV bytes with CRLF and header
id,name,email,stage,owner,revision, using customers() order and current values.
Protect spreadsheet formula cells beginning = + - @ tab CR LF with a leading
apostrophe before CSV quoting. mechanics(): counts only customers/tasks/articles,
no row contents, credentials, user chat or raw exceptions.

## HTTP/runtime contract (implement app.py + static/*)
Use protected security.py API documented separately. Bind only127.0.0.1; preferred
port comes from env PORT, fallback on EADDRINUSE to OS-assigned free port, never3000.
Use APP_RELEASE and DATA_DIR from operator, no user path input. Write readiness
JSON {pid,port,release} to DATA_DIR/ready.json only AFTER actual socket binding and
store/security initialization. No stale readiness from an earlier process.

Routes:
- GET /healthz always200 {'status':'ok','release':APP_RELEASE} without DB mutation.
- GET /readyz probes a real write transaction:200 {'ready':True}, or503 safe error.
- POST /api/login {username,password}: protected login, HttpOnly/Secure/SameSite
  cookie, session user+csrf response. Wrong credentials401; malformed payload422.
- GET /api/session authenticated user+csrf; POST /api/logout needs CSRF/origin,
  deletes session, clears cookie. Session remains valid through service restarts.
- GET /api/dashboard, /api/customers?query=&stage=, /api/tasks?customer_id=,
  /api/articles, /api/{customers|tasks|articles}/<id>; authenticated and scoped.
- POST /api/{customers|tasks|articles}: Idempotency-Key required; new201/replay200.
- PATCH /api/<kind>/<id>: JSON {revision, ...patch};200 record wrapper.
- DELETE /api/<kind>/<id>: JSON {revision};200 deleted wrapper.
- GET /api/customers/export.csv: authenticated scope, exact CSV attachment.
- GET /news: unauthenticated {'articles':public_articles()}.
All state-changing authenticated routes require correct X-CSRF-Token AND Origin
matching the configured HTTPS origin;403 on missing/invalid. Do not trust X-Role,
X-Owner or injected owner/body role. Protected security rejects forged headers.
Missing/invalid authentication401, foreign record404, conflict409, validation422,
busy503. Response {'error':safe_enum} only for errors; body limit32KiB ->413.
JSON arrays, unknown fields, path traversal, wrong methods and malformed JSON must
not crash the process or disclose files/config. Static serving has an explicit
allowlist; no directory listing or DATA_DIR/source/config serving. No raw request
or exception logging. No cookies, password/hash, CSRF, private config in logs.

Frontend / provides real login, dashboard, customer list/search/stage filter,
create/edit/delete, task list/create/status/delete, article list/edit/publish/delete,
logout and CSV download. DOM controls need stable data-testid or named IDs in
UI-MAP.json. UI follows the pinned reference, HARMONIKA brand, proper validation,
loading/empty/error states, keyboard focus, desktop/mobile menu and visible role
restrictions. Use actual API data. Race-safe scope/list handling and serialized
auth transports must survive delayed replies. Await final assertions in async QA.

Minimum checks include 360x780,768x900,1280x900: no document horizontal overflow,
all visible interactive targets >=40px high, visible labels, no pageerrors or
failed static assets. Narrow tables may scroll inside their own labelled wrapper.
Keep focused input, validation and submit visible; physical OS IME is not certified.

## Reports and ownership
CHECKPOINT.json exact fields: domain,release,source_sha256,service_unit,baseline_customer_id.
Obtain fields from actual ops receipts and create the baseline customer through
an authenticated tool call with key checkpoint-customer. Do not invent readiness.
RESULT.json TURN B: {scope:'dedicated_fullstack_exam',phase:'baseline',completed:true,
production_changed:false}. TURN C uses phase:'revision_recovery', completed:true,
checkpoint_preserved:true. production_changed refers to existing business/core
services; the disposable test host and owned exam DNS ARE intentionally changed.
Reports must state that distinction and disclose coaching/fixture corrections.

## Clarifications after independent contract review
Creation fingerprints are canonical JSON of validated, normalized fields after
trimming/defaults (including resolved owner); malformed input422 precedes replay.
Concurrent create may return created/replay or503 busy, with exactly one row;
retry the SAME key/payload only following an authoritative503 response.
All login POSTs require exact configured Origin. Ops and HTTPS browser supply it.
Protected security throttles failed fixture logins with bounded per-user/peer state.
PORT unset/3000 means OS free port; busy preferred port also uses OS free port.
Operator removes stale ready.json before start; app publishes readiness atomically.
Edge always uses the verified current unit PID's actual port, never a cached port.
Errors: auth401, CSRF/origin403, size413, JSON/key/unknown-field/type422 in that
order for authenticated mutations; login origin precedes size/parse/credentials.
PATCH envelope is {customer|task|article:record}, DELETE {deleted:true}.
Missing/invalid Idempotency-Key422; owner/customer_id changes on PATCH422.
CSV represents current values with the required formula prefix as sole deviation;
Content-Disposition: attachment; filename="customers.csv".
Unsupported schema>2 raises AppError(503,'schema_unsupported') on construction
before writes. V1 neither modifies schema2's reserved columns nor downgrades it.
App headers: nosniff, X-Frame-Options DENY, CSP default-src 'self';
script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'.
Edge adds HSTS max-age=86400. Browser QA runs only through public HTTPS origin.
Sessions use SameSite=Strict. Cookie credentials/CSRF never appear in receipts.
TURN B failed release corrections may use v1-r1..r3 with reproduced bug/regression;
TURN C uses v2-r1..r3. Fault/backup/rollback drills remain TURN C only.
Dashboard active means visible customers with stage=active. UI ignores stale list
responses after a newer request/scope/logout; deterministic delayed-reply QA must
await actual final assertions. Disable submit while mutation is in flight.
