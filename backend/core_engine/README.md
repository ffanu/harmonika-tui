# Harmonika Shared Core

Service privat data, memory dan workflow. Web/TUI/API menggunakan boundary
Bearer gateway `/v1/core`, bukan akses langsung ke proses ini.

## Jalankan dan uji

Python environment harus menyediakan `aiohttp` dan `cryptography`.

```sh
python -m unittest tests.test_core_data tests.test_core_state tests.test_core_regressions tests.test_core_http
```

Production memakai `harmonika-shared-core.service`, loopback18195. Secret
autentikasi `/etc/harmonika-ai/core-engine.token` berbeda dari key AES-GCM
32byte `/etc/harmonika-ai/core-storage.key`; keduanya0600, tidak masuk repo.
DB `/var/lib/harmonika-core/core.sqlite3`0600, direktori0700. Jangan mengganti
storage key tanpa migrasi/re-enkripsi; backup DB harus menyimpan key secara
terpisah dalam pengelolaan secret yang aman.

## Kontrak

- GET `/capabilities`, `/skills`, `/skills/{id}`; POST `/invoke`.
- Private gateway mengirim principal token terpercaya; input caller tidak dapat
  menentukan actor, storage path, host command atau upstream URL.
- 12 tool lokal;19 adaptor workspace dan2chat hanya dijalankan gateway Go;
  6 dokumen lokal dan60 belum tersedia ditolak secara eksplisit.
- Memory/workflow AES-GCM, TTL, kuota, idempotency dan recovery tombstone.
- Log hanya HMAC actor, status dan durasi. Tidak mencatat prompt atau isi memory.

Detail: [kontrak](../../docs/CORE-FOUNDATION.md),
[checkpoint](../../docs/CORE-FOUNDATION-CHECKPOINT.md),
[laporan](../../docs/CORE-FOUNDATION-REPORT.json).

`scripts/deploy-core-foundation.py` hanya untuk aktivasi awal setelah hash
baseline, staging dan smoke terverifikasi; script membuat backup dan rollback.
Jangan menjalankannya ulang untuk menimpa state produksi yang sudah ada.
