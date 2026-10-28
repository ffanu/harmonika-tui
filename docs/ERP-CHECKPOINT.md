# ERP checkpoint —2026-10-26

Objective: phased implementation of the supplied multi-industry blueprint.

- Blueprint read in full; all363 exact IDs,28 layers and25 profiles reconciled.
- Engine planning/source generation active through existing backend registry.
- Real core starter compiled; financial/stock/auth/RLS tests use synthetic DB.
- No existing business DB, external message, payment/provisioning or domain deploy.
- 76 source operations plus3 orchestrators callable;38 require connector work,
  249 unimplemented. Do not report enterprise/full industry ERP complete.
- Core extended: revision CRUD, quotation/order fulfillment, procurement receipt,
  vendor payments, closing/reversal and financial reports.61 Python tests,221 real
  PostgreSQL scenarios,7 Node tests and17 browser menus per viewport pass.
- Next: complete remaining source operations, then validate5 domain
  workflows before broader templates; connectors and production deployment need
  explicit service configuration/target.

Q&A: source counts disagree; actual numbering363 is authoritative. Keyword
confidence is heuristic, not probability. Theme is a classic admin adaptation.
All25 profiles generate source, but only the common core has qualified runtime
flows; special modules are scaffold/planning. Keep these boundaries visible.

- ISP extension qualified: monthly subscription billing uniqueness, frozen price,
  billing-only pause/cancel and ticket state machine. No scheduler/provisioning.
- Added10 supplied-data domain analyses and report/workflow source generators.
- Source UI now supports multi-line forms and list pagination/customer-product search.
- Source instance remains undeployed; real connectors, clinical/banking rules and
  named deployment target remain unspecified. Full blueprint is still incomplete.

- Basic manufacturing source qualified: immutable BOM, explicit cost snapshots,
  quality-gated atomic completion; concurrent stock and graph tests pass.
- Fixed duplicate/orphan forms using distinct component keys.
- Mapped bare DriverAdapterError/TransactionWriteConflict to409; no raw message
  matching, customer logging or automatic retries.
- Previous phase: release2026.10.26.9 helper and78 backend discovery entries. Gateway/native
  binary/config/user sessions untouched. No live business instance deployed.
- Next priority: real POS/cash-register workflow and further industry runtime slices;
  full blueprint, configured connectors and deployment remain pending.

- Cash POS source qualified: cashier-owned shift, server price/change, atomic
  checkout, admin full refund with explicit restock, cash close/reconciliation.
- Concurrent last-item, refund and checkout/closing races tested; actor-bound
  idempotency closes cross-cashier replay bypass.
- Typed validation errors422 and sanitized unknown errors500 preserve retry intent;
  browser injects500 and confirms identical key on explicit retry.
- Current: release2026.10.26.10;79 discovery entries;61 Python,221 PostgreSQL/API,
  7 Node tests;17 menus at desktop/mobile. No business instance deployed.
- Next: CRM/project/service workflows, then further industry/connector requirements.
 249 operations and38 connector operations remain incomplete.


## Latest CRM/project continuation

- Active release2026.10.26.11;83 discovery entrypoints. Earlier counts above are
  historical. Current readiness:41 planning/schema/theme,10 generators,29 supplied-
  data analyses,38 connectors required and245 unimplemented catalog operations.
- Qualified source adds lead ownership/stages/manual follow-ups/customer conversion,
  project/tasks/assignment, timezone-aware work time and explicit correction history.
- Authorization runs before idempotency lookup; revoked assignment cannot replay
  cached writes. Concurrent lead conversion creates one customer; per-user locking
  prevents overlap across projects. Project completion cannot race a new task.
- Verification:65 Python tests,12 Node core/auth tests,286 real PostgreSQL/API
  scenarios,19 menus at each desktop/mobile viewport; all passed. Phone hardware
  and stress/load tests remain unperformed. Parallel public calls can return
  analysis_busy; sequential requests passed.
- Public source download:48 files, all template bytes and SHA256 manifest verified.
  Generated download explicitly remains unbuilt/undeployed/untested per artifact;
  reusable template code qualified in disposable owned fixture only.
- No service restart, native binary/config/session modifications or existing business
  database access. No automatic email, invoice, payroll or external payment action.
- Next: further industry workflows and configured connectors; full blueprint remains
  incomplete. See ERP-ENGINE-REPORT.json for precise current evidence/boundaries.


## Latest project time billing and controls

- Release2026.10.26.12,86 backend entries;32 input analyses;242 catalog operations
  and38 configured connectors still unimplemented. Do not mark whole blueprint done.
- Real source billing: finance/admin preview, explicit rate/date/tax, revision/hash
  revalidation, immutable snapshots and unique entry guarantee. Existing invoice
  list/manual settlement works; no automatic GL/external payment. No credit-note
  cancellation/rebilling implemented; billed time cannot ordinarily be voided.
- OpenCode GLM5.3 independent review: no blockers. Follow-up UTC policy documented,
  project-owner read visibility tested, stale409 UI fixed; additional edge tests pass.
- 68 Python,14 Node core/auth,342 real PostgreSQL/API checks pass;19 menus desktop
  and mobile pass including new preview/change/cancel/409/recalculate/issue actions.
- New input analyses: resource capacity half-open sweep, explicit risk expected
  exposure, portfolio costs/earned value/progress. No inferred values or live writes.
- Public new analyses and50-file source download verified. All template bytes and
  SHA manifest checked. Gateway/service PIDs unchanged, native binary/config/session
  untouched. Business instance remains undeployed.
- Next: remaining source workflows and connectors, starting milestone/issue flows
  and inventory/accounting gaps. No real ERP deployment target specified yet.


## Latest milestone/issue source continuation

- Current release2026.10.26.13 and88 backend entrypoints. Prior counts are historical.
  Readiness41 planning/schema/theme +10 generators +34 supplied-data analyses,
  with240 catalog operations and38 configured connectors still incomplete.
- Source now has task-linked milestones, explicit issue ownership/assignment,
  resolution/reopen/owner verification and gated project completion. Serializable
  revision CAS protects new issue/close races; current manager checked before cache.
- Cancellation policy is explicit: cancelled tasks never count DONE; target may
  be cancelled/replaced by owner. Whole-project cancellation freezes unresolved
  history rather than pretending successful completion. Reopening is not implemented.
- Latest qualification:70 Python,14 Node core/auth,413 actual PostgreSQL/API checks,
  19 menus at each desktop/mobile viewport. Physical phones and stress tests not run.
  Scenario count can vary by one when a deliberate race requires explicit retry.
- OpenCode read-only review findings addressed by tests, documentation and UI fix.
  No Claude. No existing business database, native binary/config/session changes,
  no gateway restart. Public analyses and52-file generated source/manifest verified.
- Full blueprint remains unfinished. Next: remaining inventory/accounting and industry
  workflows, approved real scoped connectors and named deployment targets. Do not
  claim existing companies are running this source without a real deployment.


## Financial reporting active checkpoint

- Current backend93 entrypoints, helper2026.10.26.15;39 input analyses,235 catalog
  operations and38 connectors incomplete. Five report views share a validated
  posted-journal calculator; no source completeness/bank reconciliation claim.
- 87 full Python tests plus14 Node and latest422 PostgreSQL/API scenarios passed.
  19 desktop/mobile menus remain qualified;5 public report views and invalid input
  pass. Controlled parallel catalog/generate both passed after bounded pure-busy retry.
- Native binary/config/MCP/user sessions/services unchanged.52-file source verified.
- Work in progress (NOT activated): OpenCode HR analyses in isolated worktree referenced
  by /tmp/harmonika-erp-hr-worker.json; Codex XLSX export/client guards in main. Never
  copy whole worker files over main; merge only scoped HR functions/sets after review.


## Current active checkpoint: HR and XLSX

- Backend100 entrypoints, helper2026.10.26.16.46 supplied-data analyses,41 planning/
  schema/theme,10 generators and3 orchestrators.228 catalog operations +38 connectors
  remain incomplete; full blueprint NOT done. See ERP-BLUEPRINT-REMAINING.json.
- OpenCode implemented6 HR analyses in isolated worktree; Codex reviewed/merged only
  scoped additions and hardened timestamp, Unicode, global budgets, loan payment ID
  deduplication, positive installment allocation and leave simulation balance labels.
- Codex built real XLSX export and private verified CLI transport: escaped text cells,
  no numeric/formula inference, header freeze/filter, bounded OOXML/ZIP, SHA checks,
  no unknown package members/DTD/entities/external targets/macros/formulas/hyperlinks.
-121 Python tests pass. Previous qualified generated code14 Node and latest422 actual
  PostgreSQL/API scenarios retained;19 menus desktop/mobile, no errors/overflow.
- All6 HR/public XLSX operations passed, actual saved file read by Openpyxl, IDs and
  formula-as-text preserved.5 financial report views and controlled2-call concurrency
  passed. Source52-file download matches every reviewed template and manifest hash.
- Owner worker worktree/branch removed after integration; main/user changes preserved.
  No commits/pushes, no other agent sessions killed, no existing business DB/service
  restart/config/native binary/credential changes. Gateway/core service PIDs unchanged.
- Older sections above record historical counts and then-current work in progress;
  HR/XLSX are now activated. Goal remains unfinished; further inventory/accounting/
  industry workflows and configured connectors/named deployment targets are pending.


## Active checkpoint: sourcing — 2026-10-27

- Backend102 entrypoints; signed helper2026.10.27.1.48 supplied-data analyses,
  41 planning/schema/theme,10 generators and3 orchestrators.226 not implemented
  and38 connector-required source operations remain; full blueprint NOT complete.
- OpenCode implemented initial RFQ/vendor analyses in an owned isolated worktree
  and independently reviewed generated RFQ source. Codex merged scoped changes,
  hardened taxability/sample metrics and implemented/qualified source integration.
- Real RFQ source records immutable baskets/manual quotes and withdrawal/replacement,
  compares explicit goods+tax totals, and finance-confirmed award creates one DRAFT PO.
  No notifications, automatic supplier selection, approval, payment or FX execution.
- Corrected withdrawal parent-scope replay; mutable authorization before cache,
  serializable/CAS races, explicit UTC deadline/validity and late-award policy.
-138 Python tests,14 Node tests, Nest/Next builds and484 disposable real PG/API
  scenarios passed.20 menus per desktop/mobile browser, no page errors/overflow.
  Browser RFQ is mocked API; money/RLS/concurrency are separately tested on real PG.
-2 public sourcing analyses and54-file source ZIP verified byte-for-byte/hash.
  Actual generated downloads retain built/deployed/tests_run=false, as required.
- No existing business database, payment sends, gateway restart, native binary/config/
  session/credentials changes, Git commits or pushes. Remaining roadmap is explicit.


##102-entrypoint completion gate —2026-10-27

- Corrected earlier reporting:102 was callability, not independent full-contract
  qualification. OpenCode found generic planning aliases, aggregate journal overflow,
  undocumented input.data and missing orchestrator count. Codex implemented/reviewed fixes.
-41 named operation_result computations/projections now coexist with legacy result.
  Explicit branding and component controls apply to generated Next source; immutable
  schema design includes tenant/FK indexes while skipping duplicate unique/index prefixes.
- Malformed/duplicate/contradictory module requests and input complexity are rejected.
  Caller-supplied journal totals must fit int64. No real business writes introduced.
-149 Python tests include every102 isolated discovery execution and semantic regressions.
  Source Next/Nest and14 Node tests passed;485 actual disposable PG/API scenarios passed.
 20 menus at both desktop/mobile viewports; no page errors/overflow.
- Helper2026.10.27.2 signed and activated; all102 backend plugin hashes updated atomically.
  Native binary/config/other sessions and gateway service PIDs unchanged.
- Public qualification result is authoritative in ERP-ACTIVE-CONTRACTS-RESULT.json;
  a rate-limited first run was rerun with pacing rather than raising user API limits.
  Do not treat the initial incomplete run as102 public successes.
- Full363 blueprint still incomplete; existing source generation/input analyses do
  not imply live operational company instances or completion of226 pending/38 connectors.

- Final public gate complete:102/102 actual licensed API contracts passed with
  exact reviewed-output equivalence;5 public rejection paths passed (missing data,
  malformed/conflicting modules, int64 journal overflow and remote logo rejection).
  Native CLI source download54 files, full manifest/template bytes verified; doctor
  release2026.10.27.2 healthy. No real company deployment or payment action occurred.
