# Harmonika multi-industry ERP engine

Source-backed ERP planner and core source generator, installed on the Linux engine
and exposed via the existing /v1/skills gateway. This is a phased implementation
of the supplied 1,392-line blueprint, not a claim that every specialty ERP is live.

## What works

- 25 industry profiles, keyword/context detection, ambiguity/negation handling,
  dependency-aware module selection, classic admin palette/terminology.
- Core relational schema, composite tenant foreign keys, FORCE RLS policies, ERD.
- Real source ZIP: Next.js15.5.28, NestJS11.2.8, Prisma7.10.1, PostgreSQL16.
- Starter core: login/CSRF/RBAC, tenant isolation, customers/products, stock CAS,
  invoices, verified manual payments, balanced immutable journals, ledger accounts,
  idempotent mutations, and operational audit IDs without raw payloads.
- Frontend login/overview, customer/product CRUD, multiline document entry,
  source workflow actions and financial reports; paginated lists and customer/product search.
- Quotation → accepted price snapshot → order → invoice → stock fulfillment.
- Supplier → purchase order → finance approval → goods receipt → verified payment.
- ISP source: full-month subscriptions, per-period unique billing, pause/cancel
  and support ticket transitions; no network provisioning or live scheduler.
- Manufacturing source: immutable BOM versions, frozen production inputs/cost,
  release/quality/complete/cancel, atomic material consumption and finished output.
  Concurrent BOM cycle insertion and competing stock consumption are tested.
- Cash POS source: one open shift per cashier, server-priced receipts/change,
  atomic stock, admin full refunds with explicit restock, cash reconciliation.
  Checkout/refund/closing races and actor-bound idempotency are tested.
- CRM source: owner-scoped prospects/activities, explicit customer conversion,
  reassignment with access checked before cached responses; no messages sent.
- Project/service source: owner/assignee permissions, task lifecycle, timezone-aware
  time entries, cross-project overlap guard, explicit voids and completion checks.
- Project billing source: explicit time/rate selection, mandatory preview, finance confirmation,
  immutable snapshots, once-billed uniqueness and invoice/payment linkage.
- Input-only resource capacity sweep, explicit risk exposure and project portfolio measures.
- Project controls source: task-linked milestones, owned/assigned issues, explicit
  resolution and owner verification. Pending controls block project completion.
- Supplied-data financial statements: balanced posted journals, explicit account types,
  period profit versus accumulated profit, opening/closing cash and classified flows.
  Input journals are caller-asserted, not independently verified or compliant by default.
- Input-only HR: attendance intervals, explicit leave units, salary components,
  supplied overtime policy, per-claim caps and loan/payment reconciliation. No statutory
  rates, approvals, disbursement or evidence verification is inferred.
- Real bounded XLSX export uses explicit text cells, frozen header and verified ZIP.
  TUI saves the artifact privately rather than showing base64.
- Period closing, immutable journal reversal and exact ledger financial statements.
- Input-only CRM pipeline and CSAT/NPS, time-based billing estimate, safe SVG Gantt.
  These do not infer attribution, post an invoice or update CRM records.
- Input-only routing/production scheduling, explicit quality criteria, scrap/WIP
  mass balance; no automatic QC certification or plant execution.
- Input-only BOM/MRP/costing, billing calendar, FEFO, three-way match, POS cash,
  explicit discounts, timezone-aware work intervals.
- Input-only finance/inventory analysis, exact-match reconciliation, depreciation,
  budget variance, rational currency conversion and AR/AP aging; no live DB posting.

- RFQ source: frozen item names/SKUs and quantities, manually recorded supplier quotes,
  withdrawal/replacement history, comparison and explicitly selected draft PO.
  No supplier messages, automatic award, freight/discount/FX or payment execution.
- Input-only procurement quote comparison and explicit-weight vendor metrics.

## Counts (reconciled)

The source enumerates **363** IDs across28 layers and25 profiles, despite header285
and summary347. Invalid spaces in e-Faktur/e-Bupot IDs are normalized, preserving
the exact original source IDs and SHA-256.

- 41 planning/schema/theme operations available.
- 10 core source generator operations available.
- 48 supplied-data calculations available.
- 38 connector/integration/communication operations need implementations/config.
- 226 remaining operations are not implemented.
- Three additional orchestrators: plan, generate and catalog.

Thus **102 callable ERP entrypoints**, not363 operational business modules.
A profile for healthcare/mining/banking does not implement EMR, mining ops or risk.

## Terminal and public skill API

```text
harmonika erp catalog
harmonika erp plan       # JSON stdin: spec, company, optional industry
harmonika erp generate   # Same input; saves verified source ZIP in Downloads
harmonika erp analyze finance.invoice  # JSON supplied-data analysis
```

Use /v1/skills/invoke with an authorized skills/chat token, e.g.:

```json
{"skill_id":"erp_all.plan","input":{"spec":"Saya punya ISP 500 pelanggan","company":"Demo"}}
```

GET /v1/skills?engine=discovery lists source-pinned operator modules.
Generate returns a base64 ZIP + SHA and readiness; CLI validates ZIP bounds/path
safety, stores it0600, and prints only the local artifact path. No credential
needed beyond the existing licensed API key, and no new native MCP tools.

A generated ZIP explicitly has built:false/deployed:false/tests_run:false. The
qualified fixture does not certify every new artifact. Configure owner secrets,
use an isolated new DB, compile/test the exact artifact and approve its deployment.
Network/provisioning, payment sends, messages, tax filing and customer DB changes
are never invoked by these pure plugins.

## Runtime layout

- /opt/harmonika-ai/erp_engine: reviewed core/templates/catalog/spec.
- /opt/harmonika-ai/skills-runtime/erp_all.*:102 approved pure plugins.
- Operator discovery verifies hashes, loads lazily and records mechanic-only
  metadata. No core/gateway restart was needed.
- TUI helper/skill release2026.10.27.1; existing sessions may require resume to
  discover the new skill instructions. Native binary/branding unchanged.

## Tests and safety

149 Python engine/discovery tests, 14 Node core/auth tests, actual Next/Nest build,484 real
PostgreSQL/API scenarios, desktop/mobile Chromium screenshots. Tests use their own
container, two synthetic tenants, and ephemeral API; no existing finance/CS/customer
database is opened. QA container/API/tunnel are cleaned after the test.

Lockfile includes reviewed transitive overrides; the qualified source npm audit
reported0 vulnerabilities. This is a point-in-time package advisory check, not a
security/compliance certification. No default admin password, no sample invoices,
no hardcoded tax rate, no claim of WCAG/PSAK/ISO/HIPAA certification.

## Remaining roadmap

MVP foundation is active. To finish the blueprint: complete specialty CRUD/write interfaces,
automatic ledger posting rules and real bank confirmation, industry
workflows (ISP provisioning/scheduled billing, non-cash POS/full manufacturing, clinical, etc.), real
connectors, validation of current legal/tax rules, and approval-gated deployment/SSL.
No provider or deployment target was supplied for a new ERP domain in this task.
Those gaps are explicit in catalog statuses instead of false successful handlers.

Sources: official Prisma7 upgrade documentation:
https://www.prisma.io/docs/guides/upgrade-prisma-orm/v7
and Nest authorization: https://docs.nestjs.com/security/authorization


## Active entrypoint contract qualification

102 callable entrypoints =99 blueprint operations plus3 orchestrators. The catalog
now includes callable_entrypoints and orchestrators separately; the363 numbered
source rows are unchanged. Each named planning operation returns operation_result
with its own computed output; legacy result is retained for existing callers.
These remain planning/source operations, never automatic business mutations.

Branding accepts branding:{name,logo_path,primary} and components:{radius_px,
touch_target}. Logo must be a local /brand/name.png|webp|jpg path; the owner supplies
the file in generated public/brand (logo_asset_included=false). No remote fetch,
arbitrary CSS or script-bearing logo is accepted. Component radius0..16 and touch
target44..64 apply to the generated source. Index output covers actual generated
tenant/FK indexes without duplicating existing unique/index prefixes; no measured
production workload/performance claim.

Direct supplied-data API input MUST use the data envelope, for example:
{"skill_id":"erp_all.finance.invoice","input":{"data":{"lines":[{"unit_minor":"100","quantity":2}],"tax_bps":0}}}
CLI analyze wraps this automatically. The gateway schema rejects a missing envelope
before execution. All active operations reject unknown fields; module selection
rejects duplicates, malformed types and optional/exclude overlap. Shared dispatch
has bounded nesting/nodes/JSON, with field-specific validation for analysis values.
Native CLI/discovery retain their narrower64KiB transport admission limits; direct
engine processing has a1MiB ceiling for documented batch calculations.

Run tests/erp/test_contract_matrix.py for all102 isolated discovery executions and
scripts/qa-erp-active-contracts.py for every102 operation through the public licensed
API with synthetic inputs. Reports contain only operation IDs/pass/latency, never
customer histories or credentials. This gate qualifies the stated pure contracts,
not a business installation or the226 pending operations/38 connectors.
