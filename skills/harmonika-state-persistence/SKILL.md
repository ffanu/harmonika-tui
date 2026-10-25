---
name: harmonika-state-persistence
description: Audit atau perbaiki penyimpanan sesi native, resume, checkpoint dan cache metadata Harmonika TUI tanpa menyalin riwayat atau mengganggu sesi aktif.
---

Gunakan saat sesi hilang setelah restart, resume salah proyek, cache basi, SQLite terkunci/rusak, atau state perlu dipersistenkan. Periksa lokasi data dan schema aplikasi sebelum membuat store baru. Riwayat native adalah sumber utama; jangan membuat salinan chat kedua hanya untuk caching.

Perintah terpasang:

```sh
harmonika state status --project /path/proyek
harmonika state list --data-dir /path/data-sesi
harmonika state checkpoint --project /path/proyek --data-dir /path/data-sesi
harmonika state resume --project /path/proyek --data-dir /path/data-sesi
harmonika state clear-cache
```

`resume` menghasilkan array `argv` untuk dijalankan pengguna; tidak otomatis attach writer ke sesi. Jangan menjalankan argv itu ke sesi aktif tanpa arahan pengguna. Baca [kontrak penyimpanan](references/storage.md) untuk TTL, WAL, privasi dan pengujian.

Helper `tools/state_store.py` membaca metadata native secara read-only, lalu menyimpan pointer session ID per proyek bila diminta checkpoint. Ia tidak membaca isi pesan, menghapus sesi, menyimpan key, memperbaiki database, atau mengklaim enkripsi. Jangan memakai `immutable=1` pada database live untuk menghindari WAL terlewat.

Jika data native tidak terbaca, hentikan alur resume dengan status degraded. Pertahankan file rusak sebagai bukti; jangan menimpa dengan database kosong atau cache lama. Uji restore/reopen, concurrency, WAL, permission dan isolasi pada fixture sendiri; jangan kill proses atau attach sesi pengguna.
