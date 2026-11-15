---
name: harmonika-cf
description: Manage Cloudflare domains, DNS records, and account data via the Cloudflare REST API when the user provides an API token or an existing connector credential; use direct API calls instead of browser hunting, never echo secrets.
---

When the user asks about Cloudflare (domains, DNS, zones, records, caches,
SSL) and provides — or previously provided — an API token, do NOT search for
browser tabs or connectors. Call the Cloudflare REST API directly:

```sh
curl -s -H "Authorization: Bearer $CF_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?per_page=50"
```

## Decision tree (follow strictly, no wandering)

1. Token available (user provided, or stored credential)?
   → Use it immediately with the API. This is a 1-call task.
2. No token at all?
   → Say so in ONE message and ask for a token with `Zone:Read` scope
   (or offer the dashboard URL). Do not open browsers, do not scan tabs.
3. API returns `invalid_api_key` / `authentication error`?
   → Tell the user the token is invalid/expired in ONE message. Stop.

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
