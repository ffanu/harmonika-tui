# Harmonika Agent Tools

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
