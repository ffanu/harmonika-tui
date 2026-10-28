# HR and XLSX integration —2026-10-26

OpenCode GLM5.3 implemented six HR analyses plus15 focused tests in an isolated
Git worktree, without commits/deployment. Codex reviewed the AST diff, preserved
all main-branch changes, merged only the HR function/operation sets/tests and
implemented XLSX export/client validation separately. Claude not involved.

Codex follow-up hardening:
- Strict real-calendar offset timestamps; no >14h/normalized minutes or discarded
  fractional precision. Overlap remains per employee with explicit supplied offsets.
- Salary components/payment rows/global result/schedule limits prevent oversized
  payloads. No zero-value installment allocation; payment IDs cannot double count
  across loans. Recorded leave and proposed-request balances separately labeled.
- HR flags forbid inferring compliance, approvals, evidence, postings or disbursement.
- XLSX is a real7-part OOXML ZIP: all cells text, escaped XML, frozen header, autofilter,
  reproducible SHA, explicit size limits. Openpyxl independently loaded the result,
  preserved000123/large IDs and treated =1+1 as a string.
- CLI rejects wrong SHA, formula/defined-name/hyperlink/connection nodes, unexpected
  parts, external targets (even without TargetMode), DTD/entities, encrypted/symlink
  members and symlink output directories. Files0600; output contains path, not base64.

These are input analyses/export, not operational employee management, statutory
payroll, tax/BPJS filing or proof of actual bank payment. No business DB or message
provider was accessed. Public activation follows the full regression suite.
