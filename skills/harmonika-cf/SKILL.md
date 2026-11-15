---
name: harmonika-cf
description: Manage Cloudflare domains, DNS records, and account data via the Cloudflare REST API when the user provides an API token or an existing connector credential; use direct API calls instead of browser hunting, never echo secrets.
---

When the user asks about Cloudflare (domains, DNS, zones, records, caches,
SSL), do NOT search for browser tabs or connectors. Use the private vault
connector — the credential lives encrypted in the vault, never in commands:

```sh
python3 ~/.config/harmonika/access/cloudflare_zones.py
```

That prints the domain list as JSON. For DNS records of a zone, use the
`ZonesReader` class in the same module (`dns_records(zone_id)`).

## Decision tree (follow strictly, no wandering)

1. Vault connector available (`saved/cloudflare-ffanu-zoneread`)?
   → Use it immediately. This is a 1-call task.
2. User pastes a NEW token in chat?
   → Store it via `save_cloudflare_zoneread.py` (token via stdin, never argv),
     then use the connector. Remind once: chat-exposed tokens should be rotated.
3. API returns auth errors?
   → Tell the user the vault token is invalid/expired in ONE message. Stop.

Never spend more than one step before acting. If you catch yourself
"searching for a way to search", stop and use this skill.

## Secret hygiene

- Never echo the token, never put it in URLs or logged command lines.
- Pass it via environment variable in a single command block.
- If the token was pasted in chat, remind the user (once, briefly) that
  tokens exposed in chat should be rotated after use.
- Treat pasted tokens as data for THIS task only; do not persist them to
  files unless the user asks.

## Useful endpoints

| Kebutuhan | Endpoint |
|---|---|
| Daftar semua domain | `GET /zones?per_page=50` |
| DNS records domain | `GET /zones/{zone_id}/dns_records` |
| Detail akun | `GET /accounts` |
| Purge cache | `POST /zones/{zone_id}/purge_cache` (butuh `Zone:Cache Purge`) |
| Verifikasi token | `GET /user/tokens/verify` |

Response selalu punya `success` — jika `false`, baca `errors[]` dan
laporkan apa adanya. Sertakan `zone_id` dari hasil `/zones` untuk operasi
per-domain.

## Output format untuk user

Daftar domain: nama, status, plan — tabel ringkas. DNS records: type,
name, content — tabel ringkas. Jangan tampilkan JSON mentah kecuali
diminta.
