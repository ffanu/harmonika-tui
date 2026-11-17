# Harmonika Self-Hosted AI Services

Terminal-first AI capabilities, fully self-hosted (no external API keys).

## Services

| Service | Port (main server) | systemd unit | Description |
|---|---|---|---|
| Web Reader | 17896 | `harmonika-webreader` | Fetch any URL, return cleaned text. Zero deps (stdlib). |
| Semantic Embedding | 17897 | `harmonika-semantic` | fastembed multilingual MiniLM (384-dim). venv: `/opt/harmonika-tools/embedding-venv`. |
| Knowledge Base | 17898 | `harmonika-knowledge` | SQLite + vector store, uses semantic service, optional cross-encoder rerank. DB: `/var/lib/harmonika-kb/knowledge.db`. |
| Rerank | 17899 | `harmonika-rerank` | CrossEncoder bge-reranker-base. venv: `/opt/harmonika-tools/rerank-venv` (CPU torch). |
| Video Generation | 18272 (tunnel) | RTX: `harmonika-videogen` task + `harmonika-videogen-tunnel` on main | CogVideoX-2b on RTX GPU, subprocess workers. Model: `C:\harmonika\models\CogVideoX-2b`. |

## Gateway routes (chat.harmonika.id, Bearer public AI token)

- `/api/tools/webreader/read?url=<target>` — GET
- `/api/tools/embedding/embed` `/similarity` — POST
- `/api/tools/knowledge/store` `/search` — POST (`rerank: true` optional), `/list` — GET
- `/api/tools/rerank/rerank` — POST
- `/api/tools/vlm/generate` — POST; `/api/tools/vlm/tags` — GET (RTX Ollama)
- `/api/tools/video/generate` — POST `{"prompt"}`; `/status/<id>` GET; `/file/<id>` GET (mp4)

Gateway Go handlers: `*_gateway.go` (deployed at `/srv/harmonika-web-chat/current/`).

## Agent tools (tools/agent-v32.js)

- `web_read(url)` — read-only
- `kb_store(text, tags?)` / `kb_search(query)` — knowledge base
- `video_generate(prompt)` — async submit + poll (~5 min)
- `batch_run(commands[])` — parallel local commands (max 8)

Verification: `web_read` and `kb_search` may provide inspection evidence.
`video_generate` mutates state and is blocked in inspection mode. Use
`video_status` to reconcile an existing job after a timeout; a pending job never
proves completion.

## Isolation and proxy update (17 November 2026)

Knowledge reads, writes, searches and deletes are scoped to the verified public
token database ID. The gateway overwrites `X-Harmonika-KB-Owner`; caller-supplied
ownership is not trusted. The loopback worker rejects requests missing that
header. Deploy the updated gateway and worker together. The additive SQLite
migration preserves legacy rows without assigning them to an arbitrary token;
legacy unowned notes require explicit operator attribution before they become
visible. Optional reranking sees only candidates from the requesting owner.

Vision maps the gateway route to Ollama `/api/generate` and `/api/tags`, with the
correct upstream Host. Proxy error handlers are configured once at construction
to avoid request races. License and device checks remain enforced. A successful
tags probe demonstrates connectivity, not image-analysis quality or GPU inference.

## Notes

- The pre-existing `harmonika-embedding` service (port 17895, Node) is load-bearing for engine memory — do not touch.
- RTX box: `win-kraken` (10.77.0.22 via WireGuard, SSH port 22022 from public IP). Video token: `/etc/harmonika-ai/video-gen.token` on main server, `C:\harmonika\videogen\token.txt` on Windows.
- Model transfer workaround: HuggingFace downloads can fail from the RTX box (xet CDN); download on the main server, then chunked-scp via WireGuard and reassemble (`reassemble.py` pattern).
