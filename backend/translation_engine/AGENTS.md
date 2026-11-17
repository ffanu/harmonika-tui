# Private translation engine

- Keep user text, glossary, translations, raw errors, credentials and request bodies out of logs and audit. No shared chat memory, training corpus or disk text cache.
- Inference goes only to fixed loopback `127.0.0.1:17890`, authenticated `internal-agent`, `harmonika_translation=true`, no tools and `tool_choice=none`. Never accept caller endpoints, model IDs, actor identities or executable paths.
- The Go gateway owns public authentication, device/license and quota checks. This worker additionally requires the private translation token and loopback peer. Worker request actors are gateway-supplied opaque values and are HMACed again for audit.
- Maintain credential rejection, bounded input/output/chunks, shared deadlines, concurrency admission, exact placeholder/URL/numeric preservation checks, and explicit errors without partial success.
- Catalog registration and model self-estimation are not language quality qualification. Every regional translation requires review. Do not claim Google Cloud Translation, GPU models or alternate providers are active without verification.
- Run `python3 -m unittest tests.test_translation_engine` with synthetic mocked providers. Do not invoke real inference, modify production, commit or deploy without the parent/operator's authorization.
