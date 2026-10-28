# Financial report calculation review —2026-10-26

Codex implementation and final review; OpenCode GLM5.3 independent read-only review
ran the reporting tests and adversarial inputs. Claude not used.

OpenCode found no core double-entry, cutoff, monthly/accumulated profit, opening/
closing cash or integer-bound arithmetic errors. Follow-ups addressed:

- Net-zero unclassified cash transfer: now flagged in both supplied-data engine and
  generated accounting source. Tests cover separate offsetting journals AND a single
  cash-to-cash transfer; no automatic classification is inferred.
- Line budget counts all supplied data, including future-excluded journals: retained
  deliberately as a processing/DoS bound and documented/tested at10000/10002 lines.
- Balance/reconciliation differences are equation self-checks, not independent data
  verification: explicit equation_self_checks_only, source_completeness_verified:false,
  bank_reconciled:false and no-activity markers prevent that claim. Empty supplied
  journals do not establish that the actual business has no financial activity.

Other tests cover wrong types, unknown fields/accounts, invalid cash-account type,
DRAFT/unbalanced entries, duplicate IDs, signed reversal, large money beyond float
precision and aggregate int64 bounds. All amounts use integer minor strings.

Capabilities are five requested views of one validated calculation, not five
independent accounting standards. No FX/tax policy, annual closing automation,
compliance certification, live bank confirmation or actual business DB access.
Source financial regression uses only owned disposable PostgreSQL/tenants.
