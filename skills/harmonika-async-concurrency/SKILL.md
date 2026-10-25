---
name: harmonika-async-concurrency
description: Merancang dan men-debug I/O latar belakang TUI, cancellation, timeout, backpressure, serta respons basi agar input dan render tetap responsif.
---

Gunakan untuk API lambat, streaming, subprocess, task bersamaan, atau TUI membeku.
Pertahankan framework proyek: goroutine dengan message event, asyncio, atau Tokio
sesuai runtime yang sudah dipakai. Jangan memaksa migrasi framework.

Render/update UI tidak melakukan I/O berat atau menunggu model. Mulai job secara
nonblocking; kirim hasil lewat channel/queue terbatas ke event loop pemilik state
UI. Blocking SDK/CPU harus dipisahkan dari loop; ticker progres bukan delta model.
Batasi jumlah worker dan buffer. Berikan request/generation ID pada setiap event;
hasil lama setelah cancel, retry, atau pesan baru tidak boleh mengganti respons baru.

Bedakan menunggu data pertama, menerima delta, final, stopped dan error. Timeout
awal, idle dan total punya fungsi berbeda. Cancel menghentikan task milik request
itu saja; jangan hentikan sesi/proses pengguna atau mengulang request gagal tanpa
batas. Tutup stream dan task sendiri dalam finally, pertahankan teks parsial,
serta tampilkan alasan teredaksi. Retry mutasi memerlukan idempotency dan izin
yang sudah berlaku, bukan pengiriman ulang otomatis semua pekerjaan.

Baca [pola lifecycle](references/lifecycle.md) bila mengubah concurrency. Contoh
Python stdlib ada di `scripts/async_runtime.py`; itu reference controller, bukan
implementasi event loop binary produksi. Jalankan `harmonika skill async-concurrency
self-test` untuk memeriksa contoh. Gunakan juga pengujian proyek yang sebenarnya;
contoh lulus tidak membuktikan seluruh aplikasi bebas hang.
