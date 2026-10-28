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

## Harmonika full-stack template (staged integration)

Next.js workflow selects the SHA-pinned offline `harmonika-fullstack` starter.
Explicit `scaffold.nextjs` accepts `template: harmonika-fullstack`; its default
`builtin` remains available for old clients. The template excludes upstream
install/start scripts, Caddy, provider SDK and destructive db push. Generated
launcher binds loopback, rejects 3000 and retries startup address collisions.

Generation is not dependency installation/build/deployment. Domain/TLS and
reverse-proxy mapping remain operator steps. Do not expose a development server
publicly or use an unrelated tenant's database. See
`docs/FULLSTACK-TEMPLATE-INTEGRATION.json` for actual activation/testing state.
Owner activation script `scripts/activate-fullstack-template.py` is dry-run by
default and verifies baseline hashes, staged tests and zero queued/running jobs;
explicit restart permission is needed under the owner's global process rules.
