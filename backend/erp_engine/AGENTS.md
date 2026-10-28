# ERP engine scope

- Read README.md and specification.txt. Preserve the exact numbered source index
  and SHA; catalog registration is not execution readiness.
- This package builds ERP plans/source. Never open an existing customer, payroll,
  CS or finance DB as a shortcut. Runtime tests use owned disposable PostgreSQL.
- Keep live business mutations, provider sends, tax filing, provisioning and domain
  deployment outside pure discovery plugins; require configured/scoped adapters.
- Keep owner credentials out of prompts, generated kits, logs and fixtures. No
  default admin password, fabricated tax rate or compliance certification.
- Test money with integer minor units, transactional idempotency, stock CAS,
  default-deny RLS, tenant foreign keys and RBAC on a real disposable DB.
- Review dependency advisories and lock the qualified versions. Compile both apps.
- Register under erp_all.* without overriding old IDs or adding native MCP tools.
  Plugin hash + source must switch atomically; never restart another agent session.
- Update readiness/report for missing domain workflows; do not replace an
  unimplemented capability with a generic successful placeholder.
- For mutable ownership/assignment, pass an authorization callback to change();
  it must run before idempotency cache lookup. Check visibility/role there, not
  terminal state, so legitimate same-actor terminal-state replays still work.
- Project work time must use timezone-aware real calendar timestamps, per-user
  serialization across projects and explicit void history; no inferred payroll.
- Milestone completion requires same-project linked tasks all DONE. New project
  control writes must touch project revision before commit, so completion cannot
  race a new issue/milestone. Manager-only actions recheck manager before cached replay.


## Active sourcing checkpoint (supersedes historical counts)

-102 entrypoints, helper2026.10.27.1;48 input analyses,226 unimplemented +38 connectors.
-138 Python /14 Node /484 real PG/API checks,20 desktop/mobile menus qualified.
- RFQ deadline is inclusive UTC for submissions; later award requires unexpired quote.
  Manual source comparison covers goods+tax only, no freight/discount/FX; analyst
  calculator has separately explicit shipping_taxable, shipping and discount inputs.
- Parent RFQ and offer relationship authorization must run BEFORE withdrawal cached
  replay; correct same-actor terminal-state replay must still work.
- Do not auto-award or claim a DRAFT PO means approval, receipt, payment or supplier send.


## Named contract qualification gate —2026-10-27

- Existing102 entrypoints must be qualified individually, not inferred from count.
  tests/erp/test_contract_matrix.py executes all102 via isolated discovery.
  scripts/qa-erp-active-contracts.py compares actual public outputs to reviewed source.
- Preserve legacy result for clients; operation_result is named focused planning output.
  Static schema/index design is not measured workload optimization or migration execution.
- Validate optional/exclude types, duplicate and contradictory selections; explicit
  branding/components only. Logo local /brand PNG/JPG/WebP, owner-provided, never fetched.
- All journal aggregates must fit int64. Preserve domain-specific batch/error limits.
- Callable total includes3 orchestrators outside363 source rows. Do not add fabricated
  catalog source rows or equate a pure calculator/source generator with a live module.
