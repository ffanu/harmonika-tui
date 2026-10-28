# Backend toolchain setup

CPU backend foundation for the existing dual-PC Harmonika architecture. No TUI
rewrite, host upgrade, firewall replacement, GPU installation, or restart of
existing services. Codex implemented; OpenCode performed the independent review.

## Installed runtime

PC1 `/opt/harmonika-ai/toolchain-runtime/venv`; resolved requirements, local wheel
archive, SHA-256 manifest, and verification report are stored alongside it.
`setup.py --install` creates this isolated environment. Subsequent installation
validates wheel hashes and uses the exact offline lock. System Python and existing
engine environments remain unchanged. Install success is separate from skill,
model, connector, or service readiness.

`verify.py` runs synthetic document, OCR, image, audio/video, archive, SQLite WAL,
cryptography, and in-memory vector-search fixtures. It never reads customer data,
loads a GPU model, launches a browser, or sends cloud API requests.

## Active interfaces

Private service `harmonika-toolchain.service` on loopback18319, secret-authenticated
POST `/capabilities` and `/self-test`, with fixed empty JSON input. No arbitrary
commands, host paths, caller URLs or credentials can be supplied. The signed
internal terminal RPC exposes `backend_tools_capabilities` and
`backend_tools_self_test`. Existing gateway processes do not restart.

The live-discovered `backend.tools.status` skill publishes sanitized readiness
metadata, including its check timestamp. It is explicitly a snapshot, not a claim
that every installed library is an activated business skill. Re-run the verifier
and `register_status.py` to refresh it after a manual installation change.

## All 19 guide sections

`coverage.json` records every part and subsection, source hash, chosen equivalent
and corrections. The guide contains mutually exclusive TUI/framework/database
alternatives; they are not a requirement to replace the existing stack.

- Existing SQLite, gateway, scheduler, registered SSH adapters and Windows media
  workers remain authoritative. Database drivers do not migrate customer data.
- PostgreSQL/Redis/Qdrant servers, Elasticsearch/ClickHouse/MongoDB, object storage,
  RabbitMQ/Kafka/Temporal and monitoring stacks require a selected architecture,
  dedicated secrets/ports and consumers. They are **not started** by installing
  drivers; no unused public services or sample-password accounts are created.
- Model libraries alone do not provide model weights or inference. New ASR models,
  embeddings and additional LLMs are **not activated** by this installation.
- Cloud/payment/email/chat libraries have no inherited credentials or automatic
  sending permissions. They remain unconfigured until explicit adapters exist.
- Linux browser capture remains blocked pending hardware isolation qualification.
  GPU generation remains on Windows; do not change Linux DRM/GPU configuration.
- Security-scanner installation does not authorize scanning third-party systems.
  Invasive scanners, remote installers and VPN/firewall reconfiguration from the
  example script are not executed on the production gateway.

Legacy package names in the example are mapped to maintained interfaces:
`pypdf`, `google-genai`, `redis.asyncio`, and Python stdlib `asyncio/zipfile`.
There is no official pretrained `id_core_news_sm` spaCy pipeline. Alternatives
and operations that remain unconfigured are documented rather than marked ready.

Compatibility references: [Google SDK](https://ai.google.dev/gemini-api/docs/libraries),
[spaCy languages](https://spacy.io/usage/models),
[vLLM platforms](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/).
