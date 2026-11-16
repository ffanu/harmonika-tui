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
- `/api/tools/video/generate` — POST `{"prompt"}`; `/status/<id>` GET; `/file/<id>` GET (mp4)

Gateway Go handlers: `*_gateway.go` (deployed at `/srv/harmonika-web-chat/current/`).

## Agent tools (tools/agent-v32.js)

- `web_read(url)` — read-only
- `kb_store(text, tags?)` / `kb_search(query)` — knowledge base
- `video_generate(prompt)` — async submit + poll (~5 min)
- `batch_run(commands[])` — parallel local commands (max 8)

Verification modes: `web_read`, `kb_search`, `video_generate` count as inspection evidence (`--verification inspection`).

## Notes

- The pre-existing `harmonika-embedding` service (port 17895, Node) is load-bearing for engine memory — do not touch.
- RTX box: `win-kraken` (10.77.0.22 via WireGuard, SSH port 22022 from public IP). Video token: `/etc/harmonika-ai/video-gen.token` on main server, `C:\harmonika\videogen\token.txt` on Windows.
- Model transfer workaround: HuggingFace downloads can fail from the RTX box (xet CDN); download on the main server, then chunked-scp via WireGuard and reassemble (`reassemble.py` pattern).
