# Harmonika Unified Backend

Implementasi 70 ID core, orchestrator dan integrasi blueprint: **57 callable,
12 internal, 1 native WSS**, tanpa ID unavailable pada cakupan ini.
Skill spesialis mengikuti capability engine masing-masing.

- [Kontrak REST, SSE, GraphQL dan WSS](../../docs/UNIFIED-ENGINE.md)
- [Laporan aktivasi dan pengujian](../../docs/UNIFIED-ENGINE-COMPLETION-REPORT.json)
- [Schema GraphQL read-only](graphql.schema.graphql)

Fitur: job/session terenkripsi, Stop/replay, priority queue, event bus durable,
retrieval BM25 corpus operator, connector secret AES-GCM, GraphQL read-only,
serta WSS Bearer atau ticket sekali pakai. Corpus awal berisi enam dokumen
kontrak API; bukan katalog bisnis atau Vector DB.

## Runtime dan pengujian

Python menggunakan aiohttp dan cryptography environment backend yang ada.
GraphQL memakai `requirements.lock` dengan hash terpin; produksi memasangnya
terisolasi di `/opt/harmonika-ai/unified-deps/graphql-3.2.13` melalui PYTHONPATH.
Tidak mengubah dependency engine lain.

```sh
python -m unittest discover -s tests -p 'test_unified_*.py'
```

121 tes unified lulus di macOS dan Linux. Batch regresi Python 403 tes, build/test
Go, staging privat, serta acceptance HTTPS/WSS nyata juga lulus.

## Operasional

Unit: `deploy/harmonika-unified.service`. Database:
`/var/lib/harmonika-unified/state.sqlite3`. Token service dan storage key 32 byte
terpisah, permission 0600. Pertahankan storage key ketika restore; simpan backup
key pada secret management terpisah dari backup DB.

`scripts/deploy-unified.py` hanya untuk aktivasi awal. Upgrade completion memakai
`scripts/deploy-unified-completion.py` dengan baseline, backup terenkripsi,
validasi dependency terisolasi dan rollback file. Jangan menjalankan aktivasi
awal untuk menimpa database aktif. Endpoint secret publik hanya mengembalikan
metadata; scope diturunkan dari token gateway, bukan header client.

Codex: integrasi, review, hardening, transport, deployment dan acceptance.
OpenCode: implementasi extension awal serta pengujian dan review regresi.
TUI tidak diubah dalam pekerjaan backend ini.
