# Harmonika full-stack web builder

Shared authenticated backend for private generated projects. Exact 161-ID catalog
from the owner specification; source checksum is in `catalog_source.json`.

- Contracts: `../../docs/WEB-BUILDER.md`.
- Modes: analysis, artifact generation, isolated execution, registered connector.
- Queue/persistence: `core.py`; schemas/readiness: `registry.py`.
- Templates never execute on the host. Docker has no external network or GPU.
- New private service: `deploy/harmonika-web-builder.service` (loopback only).
- Gateway `/v1/builder/*` derives ownership from an authenticated public API token.
- Missing external accounts stay unavailable; generating a recipe is not deploying.

Focused local suite (install hashed `requirements.lock` in a dedicated venv):

```sh
python -m unittest discover -s tests -p 'test_web_builder_*.py' -q
```

Use the owner-run `scripts/deploy-web-builder.py` only after staged checks.
It compares the gateway baseline and preserves unrelated service PIDs. Never
reuse these deploy paths or source snapshots for unrelated services.
