# Project billing review — 2026-10-26

Codex implementation; OpenCode GLM-5.3 independent read-only review. Claude not used.

OpenCode final review found no blockers in tenant/RBAC, serializable project CAS,
entry uniqueness, preview recomputation and fiscal gates. Its specific follow-ups:

- UTC date cutoff: generated README now explicitly documents UTC issue date. A
  company timezone/calendar policy remains a future configuration requirement.
- Owner visibility: explicitly documented; owner GET200 and assigned nonowner404
  now covered by real API tests.
- Stale preview after409: UI now clears snapshot for revision/stale/closed-period
  errors. Browser injects409, asserts no automatic retry, requires new preview.
- Added tests: cancelled/planned/missing customer, >100 lines, wrong hash, changed
  date/tax, admin preview and invoice/period-close concurrency.

Independent review is source inspection, not compliance certification or load test.
All business runtime tests use disposable synthetic PostgreSQL; no existing CS,
finance, customer or payroll database accessed. No external provider action.

Design constraints: explicit positive rate, 1..100 selected time entries, half-up
integer minor money, no inferred tax, no automatic GL, manual verified settlement.
Billed time has immutable original and cannot ordinarily be voided. Credit-note/
cancellation/rebilling route is not implemented. Source is activated via existing
pure generator; a real ERP business instance has not been deployed.
