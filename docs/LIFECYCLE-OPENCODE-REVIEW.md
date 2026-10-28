# Harmonika Registry — Skill Failure Log (opencode worker report)

Worktree: `/tmp/harmonika-failure-registry` (branch `agent/failure-registry-20261025`, base `c6a9c73`).
Scope respected: only the three permitted files changed; no commits/push; no master service,
RTX adapter/queue, CLI, or production mutation; no processes touched. Root to review/apply patch.

## Changed files

| File | Change |
|---|---|
| `backend/engine_registry/failure_log.py` | NEW — `FailureLog(database_path)` utility with `.record(skill_id, error_type, latency)` / `.summary(limit=5)` and the `skill_failures` DDL single source of truth. |
| `backend/engine_registry/discovery.py` | MODIFIED — extended strict table allowlist with `skill_failures`, create table in `_ensure_schema`, wire `execute()` input-validation/exception/timeout/bad-output/cancelled failures into the log via fail-safe `_record_failure`. |
| `tests/test_skill_failure_log.py` | NEW — 21 focused tests (unit + Discovery integration). |

## Design summary

- **Schema (exact)**: `skill_failures(timestamp TEXT, skill_id TEXT, error_type TEXT, latency REAL)` — latency in milliseconds, no PK/index/extra columns. Technical-only: no identity, prompt, payload, messages, URL, path, token or payload hash ever stored.
- **Error types**: fixed 15-entry allowlist (`skill_exception`, `skill_timeout`, `invalid_input`, `invalid_output`, `cancelled`, 9× `graphics_*`, `logging_unavailable`). Any unknown/free-form string (or non-string) maps to `skill_exception`; nothing free-form is persisted.
- **skill_id**: canonical registered IDs only, supplied by the caller (Discovery passes snapshot-registered IDs). Enforced defensively with `^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$` fullmatch and length ≤ 100; non-conforming IDs are silently dropped (no row). `execute()` on an unregistered/unknown ID raises `E_NOT_REGISTERED` before any recording → no event.
- **Discovery wiring** (`backend/engine_registry/discovery.py`):
  - `E_PAYLOAD` (input validation) → `invalid_input`; `E_PLUGIN` → `skill_exception`; `E_TIMEOUT` → `skill_timeout`; `E_OUTPUT` → `invalid_output`; `E_CANCELLED` → `cancelled`; unknown codes → `skill_exception`.
  - Recorded in `execute()` catch/finally paths, off the event loop via `asyncio.to_thread`, best-effort: on DB failure a mechanical `failure_log_unavailable` warning is emitted on logger `harmonika.discovery.failure` and the original skill error/cancellation propagates unchanged.
  - Original per-skill audit (`engine_discovery_execution`) and all pre-existing behavior preserved: audit still raises on success-path audit failure, still not written for pre-validation `E_PAYLOAD`, `E_CANCELLED` semantics intact.
- **Hardening** (per operation): no symlink anywhere in the DB path chain; regular file only; `st_nlink == 1` (hardlink rejected); owner must be current uid; mode must not be group/other accessible (0o600 on create); file identity `(st_dev, st_ino)` pinned on first open so replaced/deleted databases never receive records and are never silently recreated.
- **SQLite hygiene**: short bounded busy timeout (default 500 ms, hard cap 2000 ms), short-lived per-operation connections, single write transaction (insert + retention). Journal mode / WAL retention never changed — no `journal_mode`/`wal_autocheckpoint` pragmas; existing native DB and WAL are never reset.
- **Retention**: after each write, rows older than 7 days are deleted and only the latest 1000 rows are kept (order: `timestamp DESC, rowid DESC`).
- **Summary**: `{'logging': 'ok'|'logging_unavailable', 'count': N, 'recent': [up-to-limit mechanical dicts {timestamp, skill_id, error_type, latency}]}`; limit clamped to 1–100 (default 5). Read failure returns `logging_unavailable`; missing table reports ok/0.
- **Fail-safe contract**: `record()` returns `ok` or `logging_unavailable` (policy-dropped invalid skill_id/latency return `ok` with no row); neither method ever raises or prints raw exception output.

## Test results (interpreter: system `python3` 3.9.6, stdlib only)

| Suite | Result |
|---|---|
| `tests/test_skill_failure_log.py` (new) | **21/21 OK** (~4s) |
| `tests/test_discovery_wal.py` | **4/4 OK** |
| `tests/test_engine_discovery*.py` family | **78/78 runnable OK**; 3 module-level import errors: `test_engine_discovery_gateway.py`, `test_engine_discovery_real_http.py`, `test_engine_discovery_sync.py` — all `ModuleNotFoundError: No module named 'aiohttp'`. Verified pre-existing: same failure on the pristine tree (stash test). |

Commands used:

```sh
cd /tmp/harmonika-failure-registry
python3 -m unittest discover -s tests -p 'test_skill_failure_log.py'
python3 -m unittest discover -s tests -p 'test_discovery_wal.py'
python3 -m unittest discover -s tests -p 'test_engine_discovery*.py'
```

### New test coverage (mapped to requirements)

- **Sentinel PII never leaks** — plugin raising `X_EXC_SENTINEL`, payload `X_PAYLOAD_SENTINEL`/`X_NOTE_SENTINEL`: asserts absence in `skill_failures` rows, full DB bytes (+`-wal`/`-shm`/`-journal` sidecars), stdout, stderr, and captured log records; free-form PII-laden error_type maps to `skill_exception` and never persists.
- **Unknown ID no event** — `E_NOT_REGISTERED` for missing/unregistered IDs → zero rows; direct `record()` with invalid IDs (case, length 101, spaces, newline, injection strings, non-string) → zero rows.
- **Timeout / bad output** — `E_TIMEOUT` → `skill_timeout`; non-dict output `E_OUTPUT` → `invalid_output`; also `E_PLUGIN` → `skill_exception`, `E_PAYLOAD` → `invalid_input`, cancellation → `cancelled`.
- **DB busy isolation** — external `BEGIN IMMEDIATE` holder: `record()` → `logging_unavailable` (bounded wait); through Discovery, failing skill still raises exactly `E_PLUGIN` with only mechanical `failure_log_unavailable` warning; after release, recording resumes; audit behavior unchanged.
- **Retention** — 1005 events → exactly latest 1000 kept (oldest evicted deterministically); rows older than 7 days purged, newer kept.
- **Schema exact 4 fields** — `PRAGMA table_info` names/types/order (`TEXT/TEXT/TEXT/REAL`), no PK, no extra tables/indexes, file mode 0o600; same assertions for the Discovery-created table.
- **Concurrency** — 8 writer threads × 10 records + concurrent reader: no exceptions, `count == ok-statuses`, `quick_check ok`, summary consistent.
- **Hardening** — symlink DB, symlinked ancestor, group/other mode bits, hardlinked DB, replaced file (identity mismatch), directory-as-path, deleted DB not recreated.
- **WAL preservation** — WAL DB stays WAL after FailureLog/Discovery writes, `quick_check ok`; existing registry tables/audit unchanged; strict allowlist still rejects rogue tables and accepts `skill_failures` on cold reopen.

## Notes for root

- `FailureLog` is instantiated internally by `Discovery` (`engine._failure_log`) using the shared registry DB; the master service / CLI may expose `summary()` separately (Codex scope).
- The `graphics_*` allowlist entries are accepted but not produced by the registry executor; they exist for the RTX adapter/queue integration.
- Deps for the 3 skipped modules: `aiohttp` (not installed in the system interpreter used here; also absent in `/tmp/harmonika-qa-venv`). No new dependencies were introduced by this change.
