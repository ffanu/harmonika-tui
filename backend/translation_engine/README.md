# Harmonika private translation

Shared backend for text translation, regional register preferences, model-estimated
language detection, and bounded batches. Web and TUI call the public Go gateway;
they never receive the worker token or the internal inference secret.

Run from the repository root with `aiohttp` available:

```sh
python3 -m backend.translation_engine.service
python3 -m unittest tests.test_translation_engine
```

The worker binds only `127.0.0.1:17918`. Set
`HARMONIKA_TRANSLATION_TOKEN_FILE` to a private file containing a token of at
least 16 characters; default `/etc/harmonika-ai/translation-engine.token`.
`HARMONIKA_TRANSLATION_AUDIT_DB` defaults to
`/var/lib/harmonika-translation/audit.sqlite3`. Use a private service user and
directory. The existing inference secret is read privately from
`/etc/harmonika-ai/internal-agent.token`. This module does not provision secrets
or activate services.

Every route requires `X-Harmonika-Translation-Secret` and a loopback peer.
Only the authenticated gateway may supply the optional
`X-Harmonika-Translation-Actor`; it is re-HMACed before audit storage. Routes:

| Method / path | Input / result |
|---|---|
| GET `/health` or `/healthz` | Mechanics, limits and RAM timestamp of last valid inference response |
| GET `/languages` | Registered codes, names, aliases, regional flags and quality limitations |
| POST `/translate` | `{text, target, source?: "auto", tone?, register?, glossary?}` |
| POST `/detect` | `{text}`; reports sampled size and model-estimated language or null |
| POST `/batch` | `{items: [translation input, ...]}`; validates all items before inference |
| POST `/invoke` | `{skill_id, input}` for `translation.translate`, `.detect`, `.batch`, `.languages` |

Example request data (send through an authenticated client, not a secret-bearing
shell argument):

```json
{"text":"Selamat pagi, {name}.","source":"id","target":"jv","register":"krama","glossary":{}}
```

`tone` is `neutral`, `formal`, or `informal`. Javanese registers are `ngoko`,
`krama`, `krama_inggil`; Sundanese registers are `loma`, `lemes`; Balinese
registers are `andap`, `alus`. `neutral` works with all languages. Target aliases
such as `jawa krama`, `krama inggil`, `ngoko`, and `sunda lemes` preserve that
register preference unless `register` is supplied explicitly. `glossary` is an
object with at most 64 source-to-translation pairs, each string at most 160
characters. It remains quoted task data, not instructions.

Requests are limited to 256 KiB JSON and 40,000 text characters. Batches contain
1–16 items and share the 40,000 character limit. Translation chunks are at most
4,000 characters and 32 chunks per request, split without breaking protected
tokens and with boundary whitespace retained. All chunks share a 120 second
deadline; two requests can execute concurrently. Detection uses the first
bounded chunk and explicitly reports whether that samples the complete input.
Equal canonical source/target returns the exact original text without inference.

Inference uses the existing Harmonika text adapter at fixed loopback port17890
with no tools, no automatic chat memory, and the private authenticated
`harmonika_translation` mode. That mode must be installed in the planner before
activation so proper names remain intact. This module does not enable Google
Cloud Translation or install a separate translation model. It rejects credential
literals and validates placeholder, URL and numeric-token preservation. Malformed,
empty, partial or preservation-invalid model results fail with fixed error codes;
they are never replaced by invented translations. A failed batch returns an error,
not a partial list. Earlier inference calls are not automatically replayed.

The catalog describes registered languages, not independently verified language
quality. Results declare `quality: "model_self_estimated"`; regional languages
always declare `needs_review: true`. Review low-resource languages, dialects and
speech levels with a competent speaker. No confidence score is fabricated.
Health `inference_verified` becomes true only after a valid actual inference
response since worker startup. Catalog reads and passthrough do not set it.
The timestamp is historical connectivity evidence, not a live provider probe or
language quality qualification; `language_quality_qualified` remains false.
`active_requests` is a live admission count for safe operations. When inference
returns the exact source unchanged, `translated` is false, `needs_review` is true,
and `review_reasons` contains `output_matches_source`. This can be legitimate for
proper names, but it never proves successful translation into another language.

Public endpoints on `https://chat.harmonika.id`:

```sh
curl --fail-with-body https://chat.harmonika.id/v1/translation/translate \
  -H "Authorization: Bearer $HARMONIKA_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"text":"Selamat pagi","source":"id","target":"jv","register":"krama"}'
```

Existing device binding, license checks and per-key quota apply. The same prefix
exposes `/detect`, `/batch`, `/languages`, `/health` and `/invoke`. Use the
`X-Harmonika-Device-ID` header if your existing token is device-bound. Never put
keys in URL queries or frontend source. Native MCP exposes `translate_text`,
`detect_language`, `translate_batch`, and `translation_languages`; API clients may
also use imperative text such as `Terjemahkan ke bahasa Jawa: Selamat pagi` via
normal OpenAI-compatible chat/SSE. The worker remains loopback-only.
Explicit member-web text translation routes to this worker even when the chat
is in Google mode. It omits unrelated roadmap enrichment and preserves literal
names in trusted worker responses. Owner-scoped `translation_message_receipts`
records only message ID, user ID and timestamp so normal chat history can retain
those translations faithfully after reload; it does not copy the message text.
Only a saved assistant message with a verified core response may receive a receipt.

The current 197-entry catalog includes 14 Indonesian regional language options:
Acehnese, Balinese, Batak Toba/Simalungun/Karo, Betawi, Banjar, Buginese, Gorontalo,
Javanese, Madurese, Makassarese, Minangkabau and Sundanese. These are experimental
model capabilities, not universal or independently verified fluency. Live samples
on 17 November 2026 found an empty Simalungun result and unchanged Indonesian for
one Gorontalo request; those outcomes must be reported, never presented as proof
of a correct translation. No paid Google credentials or separate model downloads
are needed for this existing Harmonika inference adapter.

SQLite WAL audit stores only timestamp, skill ID, error enum, latency and optional
actor HMAC, capped at 1,000 rows. No translation text, glossary, raw exception,
credential, chat history or dataset is stored or logged. aiohttp access logging
is disabled. The engine does not read other users' sessions or protected network
infrastructure.
