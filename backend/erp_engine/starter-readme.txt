# Harmonika ERP core starter

Generated source for Next.js + NestJS + Prisma 7 + PostgreSQL. This is a core
starter, not a validated hospital, banking, MRP, tax filing or network provisioning
system. Industry profiles customize theme and module planning; specialty workflows
and external payment/WA/DJP connectors are NOT implemented. No real customer data,
accounts, default admin password or fabricated transactions are seeded.

Core: tenant-scoped auth/RBAC/CSRF, customers, products, atomic stock adjustment,
issued invoices with explicitly supplied tax basis points, verified manual payments,
balanced immutable journals, ledger accounts, idempotent mutations, mechanic audit.
Quotation/order price snapshots, invoicing/stock fulfillment, suppliers, purchase
approval/receipt and verified vendor payment are implemented. Period closing and
journal reversal preserve posted history; financial statements use ledger entries.
Money uses integer minor units as strings (e.g. 100 = 1.00 currency unit), not float.
Invoice payment does not automatically create a journal; reconcile explicitly.

Setup (new isolated database only):
1. Use Node22, PostgreSQL16. Set unique DB owner/runtime passwords, JWT_SECRET >=32
   random chars, APP_ORIGIN and owner bootstrap fields. Do not reuse production DB.
2. npm ci; npm run build; npm test.
3. DATABASE_URL as migration owner: npm run db:init -w apps/api (fresh DB only).
4. Runtime role erp_app must NOSUPERUSER/NOBYPASSRLS. ops/init-runtime-role.sh gives
   default privileges before creating tables. All tenant tables FORCE RLS; every
   ORM transaction sets app.tenant_id locally. Never run normal API as DB owner.
5. ALLOW_BOOTSTRAP=true and owner DB URL: npm run bootstrap -w apps/api, once per
   intended tenant; this creates an owner, without sample business transactions.
6. Start API with runtime DB URL, secret and same-origin configuration. Frontend
   rewrites /api to API_INTERNAL_URL. Secure cookie defaults true; only isolated
   local HTTP tests may explicitly use COOKIE_SECURE=false. No CORS wildcard.
7. npm run start -w apps/api; npm run start -w apps/web.

Docker Compose template is provided; initialize schema/RLS/bootstrap using a
one-off operator command with migration-owner DB URL before starting API. Do not
publish database or API ports. Web binds loopback; attach an approved HTTPS reverse
proxy after testing. Docker image build needs the supplied package-lock.json.

Writes require Idempotency-Key (8..80 letters/digits/_/-), CSRF + Origin for cookie
auth; bearer API use still verifies database user and tenant. Viewer cannot mutate.
Idempotency replay with a different payload conflicts. Serialization conflicts are
409 and require a conscious retry, not blind repeat. Product stock mutations use
revision CAS and nonnegative quantities. Invoice and journal entries cannot be
edited/deleted through API. Manual payments require an authorized operator's
verified:true; this is not evidence that a gateway or bank confirmed payment.

UI provides login/overview, customer/product create/edit/soft-delete, stock
adjustment, quotation/order actions, purchase approval/receipt, supplier creation,
invoices/payments, accounts/journals/reversal, period closing and financial reports.
Document creation UI and API support up to100 product lines.
Payment, stock and reversal actions ask for explicit confirmation. No external
bank transfer, physical delivery or provider send is triggered by these buttons.

API routes (cookie session + CSRF, all changes require Idempotency-Key):
- /customers, /products: GET/POST; /:id PATCH/DELETE uses revision CAS.
- /products/:id/stock POST: delta, reason, revision.
- /sales/documents GET/POST; /:id/{confirm,convert,invoice,fulfill,cancel} POST.
- /suppliers GET/POST; /purchases GET/POST.
- /purchases/:id/{approve,receive,cancel} POST: revision,confirmed:true.
- /purchases/:id/payment POST: amountMinor,verified:true (manual record only).
- /invoices GET/POST; /invoices/:id/payment POST.
- /ledger GET; /ledger/accounts GET/POST; /ledger/journals POST.
- /ledger/journals/:id/reverse POST: period,memo,confirmed:true.
- /ledger/periods GET; /ledger/periods/close POST: period,confirmed:true.
- /reports/financial?period=YYYY-MM GET; /audit GET.

A closed period also prevents backdated journals in earlier periods, preserving
the snapshot. A reversal is posted in an open period and never deletes the source.
Cash-flow categories are explicit; unclassified amounts are flagged. Operational
invoices and payments are not auto-posted to the ledger. Get/list endpoints are
bounded and paginated (page1..1000, limit1..100, array responses); customer/product
q search is tenant-scoped. Accounts/periods have their own caps. Reference dropdown
search across pages remains pending.

Remaining: non-cash POS, scheduled/prorated billing, payroll, remaining industry workflows, SSO/MFA,
connector execution, deployments/SSL and compliance certification. Source artifacts
are not production-ready merely because generation passes. See docs/plan.json and
docs/READINESS.json; validate the exact generated project.

ISP extension: subscriptions snapshot product price, require explicit full_month
billing policy and tax basis points. Per-period invoice uniqueness protects
concurrent requests and different idempotency keys. Billing status changes do not
change network connectivity. Support tickets enforce controlled resolution/closing
states and tenant-scoped active assignees. No RADIUS/OLT/WA calls or automatic
billing scheduler are configured. See /isp/subscriptions and /isp/tickets routes.

Manufacturing extension:
- GET/POST /manufacturing/boms: outputProductId, version, components containing
  productId, quantity per finished unit and operator-supplied unitCostMinor.
  Versions are immutable. All stored versions must remain acyclic; synchronous
  validation is limited to500 BOM versions/10000 edges and rejects beyond capacity.
- GET/POST /manufacturing/orders: bomId,quantity. Component quantities and costs
  are copied from that version. Material estimates are not inventory valuation
  and are never taken from product sales prices. No stock is reserved at creation.
- POST /manufacturing/orders/:id/release or /cancel: revision,confirmed:true.
- POST /manufacturing/orders/:id/quality: revision,result PASSED/FAILED,note,confirmed:true.
  Explicit operator inspection is required. This does not certify a quality standard.
- POST /manufacturing/orders/:id/complete: revision,confirmed:true; requires PASSED.
  All material consumption and finished output commit together or all roll back.
- Operator/admin may mutate; finance may review. Products referenced by immutable
  BOMs cannot be soft-deleted. Tenant foreign keys and FORCE RLS apply to every table.

Source UI includes BOM/production pages when module manufacturing is selected.
It is a basic integer-unit discrete production workflow; no fractional/process
manufacturing, automatic work-center execution, partial completion/scrap posting,
GL/WIP valuation or manufacturing regulatory certification is implemented.

Database write conflicts (Prisma P2034/adapter TransactionWriteConflict and
PostgreSQL40001/40P01) return409 transaction_conflict. No message parsing or automatic
transaction retries. Existing idempotency safeguards remain mandatory on retries.

Cash POS extension (only when module pos is selected in the menu):
- GET/POST /pos/shifts: openingMinor,confirmed:true. At most one open shift per
  cashier/tenant. Operator sees only their own shifts; finance/admin may review.
- GET /pos/shifts/:id/balance returns expected cash from opening + net receipts
  - refunds recorded on that shift. GET /pos/shifts?own=true supports own selection.
- POST /pos/sales: shiftId,optional customerId,lines[{productId,quantity}],taxBps,
  tenderedMinor,confirmed:true. Product prices come from the server; tax is explicit.
  Cash receipt/change and all stock consumption commit together; no bank verified.
- POST /pos/sales/:id/refund: admin only; own open shiftId,restock boolean,reason,
  confirmed:true. Full refund only, exactly once. Restock requires explicit choice.
  The current drawer must have enough cash. No external transfer is sent.
- POST /pos/shifts/:id/close: own shift only; revision,countedMinor,note,confirmed:true.
  Closing stores expected/count/variance and prevents further checkout. Every sale
  or refund advances the shift revision to serialize against closing.
- An admin may refund a prior shift's sale from their own current shift. The refund
  affects current cash; the original closed shift's cash snapshot stays unchanged.
- Idempotency request hashes include actor ID. Another cashier cannot replay a
  known key to bypass ownership validation. No unsafe automatic retries.

Cash amounts are limited to18-digit nonnegative minor-unit strings. The source
records explicit human cash attestations, not proof of physical money movement.
There are no card/QR/gateway calls, cash hardware control, partial refunds, fiscal
receipts or automatic accounting entries. Those need separate configured modules.

Error/retry boundary: only typed Harmonika validation errors produce422. Unknown
infrastructure/internal failures produce500 with a sanitized code, so the UI
retains the same idempotency intent. A user-confirmed retry reuses that key.
No raw SQL/error messages or user payloads are returned/logged by this filter.

CRM / project-service extension:
- GET/POST /crm/leads: name, optional email/phone/company/potentialMinor. Operators
  see owned leads; admin sees all. Finance does not see unconverted private leads.
- POST /crm/leads/:id/stage: revision,stage,confirmed:true. NEW→QUALIFIED/LOST;
  QUALIFIED→LOST; LOST→NEW. WON is reached only by explicit conversion.
- POST /crm/leads/:id/convert: revision,mode new/existing,optional customerId,confirmed.
  Existing customer must belong to the same tenant. New mode copies provided lead
  name/email; phone/company remain on the lead. No name/phone dedupe guess or send.
- POST /crm/leads/:id/assign: admin,revision,ownerId,confirmed:true. Owner must be an
  active operator/admin. Ownership is rechecked BEFORE idempotency cache lookup.
- GET/POST /crm/leads/:id/activities: kind NOTE/CALL/MEETING/FOLLOW_UP,summary,optional
  dueAt with timezone; FOLLOW_UP requires a date. These are manual records only.
- POST /crm/activities/:id/complete: revision,confirmed:true.
- GET /work/assignees: tenant operator IDs/email/role only, never passwords/tokens.
- GET/POST /work/projects: name,optional customerId,budgetMinor,startsOn,dueOn (real
  YYYY-MM-DD dates). Project owner is current actor. Owner/assigned operators see
  authorized projects; finance/admin may review. All lists remain paginated.
- POST /work/projects/:id/state: revision,status ACTIVE/COMPLETED/CANCELLED,confirmed,
  reason for cancellation. Only owner/admin; unfinished tasks block completion.
- GET/POST /work/projects/:id/tasks: title,optional assigneeId,dueOn. Owner/admin create.
- POST /work/tasks/:id/assign: revision,assigneeId,confirmed. Manager only; active user.
- POST /work/tasks/:id/state: revision,status IN_PROGRESS/BLOCKED/DONE/CANCELLED,
  confirmed; BLOCKED/CANCELLED require reason. Cancellation is manager-only.
- GET /work/projects/:id/time; POST /work/tasks/:id/time: startedAt,endedAt,note.
  Explicit timezone and whole seconds; positive duration≤24h, no future end.
  Project must be ACTIVE and task IN_PROGRESS. Actor is current user, not a body ID.
  Per-user serialized validation rejects overlaps across projects. Adjacent time is
  allowed. Dates/budget do not imply salary, cost, bill rate or tax.
- POST /work/time/:id/void: reason,confirmed. Recorder/admin only on active projects;
  original time/note are retained. Reassignment may revoke access to old task data.
- GET /work/projects/:id/summary: owner/admin/finance sees task totals and non-voided
  seconds; no inferred cost or invoice. Completion/child mutations serialize on
  project revision. Auth DB failures propagate as infrastructure failures, not401.

Reference dropdowns show bounded first pages. No CRM external messaging, reminder
scheduler, full opportunity/marketing automation, task dependency runtime, project
invoicing, payroll posting or regulatory certification is implemented by this slice.
Source forms use device-local date/time inputs converted to explicit UTC instants;
calendar project dates stay date-only. Physical-device/DST UX has not been qualified.

Project time billing extension:
- GET /work/projects/:id/bills returns immutable time/rate snapshots and linked
  invoice totals/status to finance/admin or the project owner.
- POST /work/projects/:id/billing-preview takes revision,issuedOn (real UTC calendar
  date, not future),taxBps,lines:[{entryId,rateMinorPerHour}]. Finance/admin only.
  Select1..100 nonvoid, unbilled time entries from this customer-linked active or
  completed project. Rates are explicit positive integer minor units per hour.
- Preview uses exact integer arithmetic: amount per entry = half_up(seconds*rate/3600),
  subtotal is sum of rounded entries, tax is half_up(subtotal*taxBps/10000).
  It creates no invoice. Returns rows,subtotal,tax,total and previewHash.
- POST /work/projects/:id/bills accepts the same input plus previewHash,confirmed:true
  and Idempotency-Key. Server revalidates the current revision, source time and
  selected rates. Invoice, bill and snapshot lines commit atomically. A unique
  tenant+entry constraint prevents duplicate billing across different keys.
- Once billed, time cannot be ordinarily voided. No invoice cancellation/credit-note
  or rebilling route exists yet; retain the immutable source and use an approved
  future correction workflow. Invoice settlement uses the existing manually
  verified payment route; it does not initiate a transfer or automatically post GL.
- Closed fiscal periods block new project invoices. Billed service lines appear in
  /invoices.workBills; they are distinct from product InvoiceLine records.
- The browser requires a preview and explicit confirmation. Changing selected
  entries, tariff, date or tax clears preview. Repricing/revision conflicts require
  reload and a new preview, not automatic retry. Dropdowns/list views are bounded;
  cross-page selectors and company-specific timezone/calendar policy are pending.

Project targets and issues:
- GET/POST /work/projects/:id/milestones; create title,dueOn,taskIds (1..50 distinct
  noncancelled tasks in this project). Owner/admin creates; owner/admin/finance reads.
- POST /work/milestones/:id/state: revision,status REACHED/CANCELLED,confirmed:true;
  cancellation requires reason. REACHED requires ACTIVE project and every linked
  task DONE. Target task links are immutable; reached/cancelled targets are terminal.
- GET/POST /work/projects/:id/issues; create subject,description,priority
  LOW/NORMAL/HIGH/CRITICAL and optional active tenant assigneeId. Owner/admin creates.
  Assigned staff can view their own issues/project, not manager-only milestone data.
- POST /work/issues/:id/assign: revision,assigneeId,confirmed:true, owner/admin.
- POST /work/issues/:id/state: revision,status,confirmed:true. OPEN -> IN_PROGRESS
  -> RESOLVED -> CLOSED; RESOLVED may reopen IN_PROGRESS (old resolution cleared).
  RESOLVED requires resolution text; only owner/admin may verify CLOSED. All issue
  state work requires ACTIVE project. Closed issues are immutable.
- Pending milestones or issues not CLOSED block project completion. New tasks,
  issues, milestones and state changes serialize through project revision CAS;
  competing close/create requests cannot commit contradictory results.
- Current authorization (including manager-only actions) is checked before cached
  mutation replay. Role/assignment loss cannot replay former privileged operations.
- Project cancellation freezes historical open controls; reopening projects,
  milestone link edits, notification/reminder delivery and uploads are not implemented.

Cancellation policy: a cancelled linked task is never counted DONE. If scope changes,
explicitly cancel the pending target with a reason and create a replacement target
with the remaining tasks. Cancelling an entire project freezes unresolved history
rather than claiming issues resolved or targets reached. Read-only history remains
available. Reopening/correcting cancelled projects is intentionally not provided.


## RFQ sourcing source

The Permintaan Penawaran menu records an explicit currency and frozen product
name/SKU/quantity basket. Owner/admin records complete supplier offers with explicit
prices, tax basis points, validity date and manual evidence attestation. The system
does not verify supplier documents. Up to20 active /100 total historical offers
are allowed per RFQ; one active offer per supplier. Withdraw with a reason before
recording a replacement under a new reference; history is retained.

Dates use UTC calendar days, inclusive of dueOn/validUntil. New offers stop after
the RFQ due date; finance may award later if the chosen offer remains valid.
Comparison covers goods plus explicitly supplied tax only, excluding freight,
discounts and FX. Same-cost offers are shown without automatic selection.
Finance/admin must choose an offer with reason and confirmation against a current
comparison hash/revision. This atomically creates one DRAFT purchase order, not
approval, goods receipt, payment or notification. Award/cancel freezes the RFQ;
ACTIVE offer records remain history and are no longer eligible for award.
Stale forms clear the comparison and refresh reads without retrying a write.
Legacy purchase orders may have no currency; RFQ-derived orders carry the explicit
RFQ currency. No base currency or conversion rate is inferred.
