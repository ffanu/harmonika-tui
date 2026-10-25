# Mobile engine

Shared private module mounted by `master_engine.service` under `/mobile/*`.
Public gateway exposes `/v1/mobile-builder/*` using token auth and original quota.

- `catalog_source.json`: 214 canonical IDs + frozen-source hash.
- `planning.py`: bounded spec/stack/architecture analysis.
- `generators.py`: pure source generation (no host I/O).
- `registry.py`: capability/readiness, source paths and size checks.
- `public_ai_mobile.go`: strict public gateway adapter.

Run tests from repo root using the dedicated aiohttp test environment:
`python -m unittest discover -s tests -p 'test_mobile_*.py'`.

Runtime build/sign/store/purchase operations remain disabled until authorized
workers/connectors are installed and tested. See `docs/MOBILE-BUILDER.md`.
