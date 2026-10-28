---
name: harmonika-erp
description: Plan multi-industry ERP modules, theme and schema, generate a real source ZIP, and distinguish implemented core from unavailable domain workflows.
---

Use for ERP building from business specifications, not operating existing company
finance/customer databases. Never create a duplicate fin/CS deployment implicitly.

1. Ask only for missing industry/company requirements if the detector is ambiguous.
2. Use `harmonika erp plan` with JSON stdin: spec, company, optional industry (one
   of 25 registered IDs). The engine selects dependencies and a classic admin theme.
3. For a requested starter, `harmonika erp generate` with the same JSON stdin saves
   a private ZIP in Downloads/harmonika-ai/zip. Report the actual artifact path.
   Do not paste ZIP/base64 or a huge source JSON into the chat.
4. `harmonika erp catalog` shows readiness. There are 363 source IDs, not 363 fully
   operational modules. 100 callable entrypoints include three orchestrators.
5. Core source supports auth/RBAC, PostgreSQL RLS, customer/product CRUD with revision checks, quotation/order fulfillment, procurement,
   atomic stock, invoices, verified manual payments, balanced journals, financial reports, closing periods and reversal. ISP source also supports monthly subscription billing and support ticket states.
   Network provisioning, EMR/payroll, full manufacturing, and bank/WA/DJP connectors
   remain unimplemented/unconfigured.
6. A source artifact is NOT a built/tested/deployed ERP. Use an isolated project,
   the artifact's README and private synthetic DB for qualification. Do not claim
   source-specific tests passed unless actually run. Existing databases are outside scope.
7. No fabricated tax rates or compliance certification. Use operator-verified rules.
   No suspension, payments, tax submission, network provisioning or external messages
   without the corresponding configured connector and explicit action authorization.
8. Deploy only to a named, approved target/domain after reviewing the concrete build.
   A registered SSH capability alone does not authorize provisioning or deployment.
9. The engine is available through existing /v1/skills endpoints, without new MCP
   tools. Keep tool counts below the gateway ceiling of 32.

CLI analysis accepts only supplied data: `harmonika erp analyze finance.invoice`,
`finance.journal.entry`, `finance.general.ledger`, `inventory.stock.management`,
`finance.reconciliation`, `finance.depreciation`, `finance.budgeting`,
`finance.multi.currency`, `report.customer.aging`, or `report.vendor.aging`.
Reconciliation uses exact unique reference/amount/direction/currency matching; it
never certifies bank settlement. Depreciation and currency use caller-supplied policy.
Results are input calculations; no posting or lookup in live business databases.

Additional supplied-data operations (same `harmonika erp analyze <id>` contract):
- `isp.billing.cycle`: calendar dates, monthly anchor preserved across February.
- `mfg.bom`, `mfg.mrp`: integer-unit DAG requirements; reject cycles/quantity explosion.
- `mfg.production.costing`: material/labor/overhead totals with explicit remainder.
- `inventory.stock.opname`: physical vs recorded deltas, without posting adjustments.
- `inventory.batch.tracking`: FEFO allocation; exclude expiry on/before as-of date.
- `procurement.three.way.match`: exact item, quantity, price AND currency matching.
- `retail.promotion`: one explicit capped discount, no assumed tax.
- `pos.cash.management`: opening + cash in - cash out vs counted cash.
- `pm.time.tracking`: timezone-aware, non-overlapping work intervals.
Use these automatically when the supplied task/data fits. A calculation is not
a completed operational workflow, reservation, payroll posting, payment or compliance check.

Manufacturing source now includes immutable BOM versions, production input/cost
snapshots, explicit operator QC, release/cancel/complete transitions and atomic
stock movements. This is a basic discrete/integer-unit production flow, not a
full factory/MES, costing ledger or manufacturing compliance certification.

Additional input analyses: `mfg.routing` (dependency critical path),
`mfg.production.scheduling` (single-machine serial scheduling, not optimal),
`mfg.quality.control` (supplied measurements/reject counts),
`mfg.scrap.management` (integer material mass balance). Nothing is posted.

Cash POS source includes open/close shifts, server-priced sales with change,
stock/cash reconciliation and admin-only full refunds. Cash is operator-attested;
no bank/gateway verification, transfer, tax document or GL posting is claimed.
`pos.transaction` is a supplied-data receipt calculator (lines,tax_bps,tendered_minor);
its `sale_recorded` and `cash_received_verified` remain false.

CRM/project source: scoped leads, manual activities, explicit new/existing customer
conversion, manager/assignee task access and timezone-aware time records. Reassignment
revokes access before idempotency replay. Time is not automatically payroll/invoices.
Input analyses: `crm.pipeline`, `crm.satisfaction.survey`, `pm.project.billing`,
`pm.gantt.chart` (escaped/validated SVG, relative minutes, no machine capacity).
Pipeline probability is caller supplied, never a model confidence/forecast guarantee.

For pm.gantt.chart, use the SVG as an artifact: save through existing file tools
when a file/preview is requested; do not paste raw SVG into the final answer.
No scheduling, customer conversion or time posting happens from input analysis.

Project time billing source includes explicit finance preview/confirmation and
immutable once-billed snapshots; generated source is not a live business instance.
Additional supplied-data analyses: pm.resource.allocation (capacity sweep with
half-open intervals), pm.risk.management (caller probability/impact, no inference),
pm.project.portfolio (explicit budget/cost/completion, no accounting postings).
Do not infer taxes, staff rates, availability, compliance or costs from these tools.

Project source now includes task-linked milestones and issue assignment, resolution,
owner closure and guarded project completion. pm.milestone and pm.issue.tracking are
also available as supplied-data analyses; their output does not modify project state.
Do not equate input analysis or generated templates with a deployed business instance.

Financial reporting analyses: finance.financial.statement, finance.cashflow.statement,
report.financial.statement, report.profit.loss, report.cash.flow. They require explicit
account types/cash flags and balanced caller-asserted POSTED journals in one declared
currency. Do not imply database verification, taxes/compliance, complete books or
live postings. Distinguish selected-month profit from accumulated profit and cash
opening/closing. Unclassified offsetting cash transactions still require classification.

ERP CLI retries only analysis_busy admission failures for the explicit pure ERP
allowlist, at most4 attempts with one stable request key. It never retries unknown
errors or other skills/business mutations. Persistent contention remains a structured
error; do not infer that an external action ran or automatically retry a payment.

HR calculations: hr.attendance, hr.leave.management, hr.payroll, hr.time.overtime,
hr.expense.claim, hr.loan.management use only explicit supplied data/policies. Never
infer wage, tax/BPJS/overtime law, payment evidence, leave approval or reimbursement.
Leave recorded_remaining_units differs from remaining_units_after_requests (simulation).
Loan payment/allocation IDs must be unique across supplied loans; amounts are not
bank-verified. Expense caps are PER CLAIM, not per employee/month. No actual HR CRUD,
payslip delivery, payroll, loan deduction or payment is executed by these analyses.

report.export.excel: JSON {title,headers:[text],rows:[[text,...]]}. All cells are text,
including amounts/IDs; no formulas or numeric inference. CLI saves verified .xlsx in
Downloads/harmonika-ai/xlsx and prints artifact_path, not base64. No macros, formula
nodes, external relationships or untrusted package parts are accepted. Request only
supplied data that the user authorized to export; do not export unrelated chat history.


Procurement analyses: procurement.rfq compares a complete explicit basket in one
currency. Require as_of, submitted_on, valid_until, prices, tax_bps, shipping_minor,
discount_minor and shipping_taxable boolean. Tax is applied after goods discount,
including shipping only when explicitly taxable. No rate/evidence verification,
award or PO is performed by this analysis. procurement.vendor.performance requires
all three explicit metric weights totaling10000 basis points and delivery records.
Zero delivered sample remains unscored; inspect delivery_sample_coverage_bps and
overdue_open_orders before describing performance. No inferred grade or sanctions.
Generated RFQ source has a separate explicit finance-confirmed draft-PO workflow;
do not confuse input-only calculations with live business mutations.


Read callable_entrypoints/orchestrators when reporting catalog totals:99 blueprint
operations plus3 orchestrators equals102 entrypoints; it is not102 live ERP modules.
Named planner operation_result is the focused answer; legacy result is broader context.
Source plan/generate accepts branding:{name,logo_path,primary} and components:
{radius_px,touch_target}. Logos are owner-supplied local /brand/*.png|jpg|webp assets,
not fetched/included automatically. Never imply production performance verification
from static index design, commercial tier assignment from heuristic planning scale,
or a generated artifact being deployed. Direct analysis API requires input.data;
CLI analyze provides that envelope.
