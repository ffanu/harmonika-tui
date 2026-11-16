# Harmonika Agent Tools

## Verified agent v3.2.1

`harmonika agent-v32` is an explicit entrypoint installed by
`python3 scripts/activate-agent-v32.py`. The default native terminal UI and its
binary remain unchanged. Node.js is required. Credentials come from
`HARMONIKA_API_KEY` or `~/.config/harmonika/harmonika-api-key`; never put keys in
the task, command arguments, project files or Git.

```sh
harmonika agent-v32 --cwd ./project --verify 'node --test test/service.test.cjs' \
  'Perbaiki layanan sesuai kontrak dan buktikan dengan tes.'
harmonika agent-v32 --resume SESSION_UUID --cwd ./project \
  --verify 'node --test test/service.test.cjs' 'TASK_ASLI_YANG_SAMA'
```

Use the same task, workspace and verification policy when resuming. Tasks and
tool output are kept in memory only; after restart the agent reads the workspace
again. Private authenticated checkpoints store mechanics and keyed hashes, not
chat, customer data, commands or credentials. Memory reuses error counters only;
it is not model training. A missing acknowledgement is reconciled with the same
request/job ID; uncertain writes and delegation jobs are never silently replayed.

Completion requires current verification. Test mode rejects failing, zero,
skipped and truncated results, changed original assertions, and verification
made stale by a later file change. It discovers built-in Node tests before
falling back to npm/Python. Set `--verify` for other runners. `--verification
artifact` checks written files by readback; `inspection` is read-only; `command`
requires an explicit `--verify` readiness command and does not claim tests ran.
An exhausted loop, failed executor or unresolved outcome returns a nonzero exit.

SSH is opt-in: `--ssh-alias staging --verify-remote --verification command
--verify 'systemctl is-active example.service'`. The alias must already have
key authentication and a pinned host key. No automatic SSH, Docker pruning,
password prompts or process signals run during preflight. Local bash executes
with the operator's OS permissions: these guards are not an OS sandbox.

Limits: 128 checkpoints per private state directory, 8192 workspace files,
128 MiB hashed workspace, 32 MiB per hashed file, 64 KiB file reads, 512 KiB
writes/tool output, 2 MiB HTTP response, and 120 seconds total model retry budget.
Limit errors remain explicit. Configure `--state-dir` outside the workspace.
Inspect `--help` for supported options. Installation keeps a private launcher
backup and activation hashes under `~/Projects/harmonika-engine/agent-v32/backups`.

Focused verification: `node --test tests/agent-v32.test.cjs` and
`python3 -m unittest discover -s tests -p test_agent_v32_activation.py`.

Agent client yang properly mengirim tool definitions ke gateway untuk eksekusi lokal.

## Files
- `agent-client.js` — v1: basic tool calling (bash, read, write)
- `agent-v2.js` — v2: PLAN → BUILD → VERIFY cycle dengan resource monitoring
- `mcp-ssh-local.js` — MCP SSH server untuk eksekusi remote via tool protocol

## Usage
```bash
# Basic
node tools/agent-client.js "deploy nginx on server X"

# With planning phase
node tools/agent-v2.js "build wireguard on 38.47.176.62"

# MCP SSH server (standalone)
node tools/mcp-ssh-local.js
```

## Architecture
```
Agent Client (this tool)
  ├── Kirim task + TOOLS ke gateway (chat.harmonika.id)
  ├── Model respond dengan tool_calls
  ├── Eksekusi LOKAL (bash, file I/O)
  ├── Kirim hasil balik ke model
  └── Loop sampai task selesai
```

## Tested Tasks
- WireGuard VPN deployment (14 tool calls)
- Nginx web server (10 tool calls)
- L2TP/IPsec VPN (16 tool calls)
- Docker containers (12 tool calls)
- UFW firewall (8 tool calls)
- Monitoring stack (11 tool calls)
- Git server + auto-deploy (8 tool calls)
- Backup system (6 tool calls)
- SSL + reverse proxy (12 tool calls)
