# Consolidated master skill engine

Exact source reconciliation + bounded defensive input analysis + isolated HTML
browser workflows. Root API contract: ../../docs/MASTER-SKILLS.md.

- `catalog.py`: OpenCode's frozen source reconciliation; not an execution claim.
- `index.py`: merged registry/readiness view including actual analyzer handlers.
- `service.py`: private authenticated stateless transport and concurrency limits.
- `browser.py`: networkless software Chromium, not a user's registered profile.
- `public_ai_master.go`: authenticated gateway and exact-ID original engine routing.

No customer/ERP/financial database access, provider secrets in requests, or
implicit activation of external/proprietary/attack tools. Mandatory tests:
`python -m unittest discover -s tests -p 'test_master_*.py' -q` in dedicated venv.

Operator auto-discovery is integrated through `engine_registry.Discovery`.
GET `/skills/discovery` exposes sanitized status; ordinary list/detail/invoke
see atomic live snapshots. No gateway restart or static ID addition is needed.
See [runtime contract](../../docs/ENGINE-DISCOVERY.md).
