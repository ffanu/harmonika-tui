# Audit read-only rtx_vision/worker.py & rtx_queue/router.py — 2026-10-25

Status: **independen, tanpa Claude**. Audit dilakukan terhadap snapshot hidup
`/Users/id/Projects/harmonika-tui` (read-only). Root mengedit `worker.py`
tiga kali selama jendela audit (09:09, 09:19, 09:22); laporan ini merekonsiliasi
temuan terhadap tiap versi. Tidak ada kode root yang diubah; patch proposal
tersendiri di `proposals/`, tes reproduksi di `tests/`.

Snapshot final yang diaudit (sha256):
`worker.py 9e817bd7…` · `router.py 8c89996a…` · `state_store.py 1cb66152…`

## Ringkasan temuan

| # | Temuan | Severity | Status |
|---|---|---|---|
| 1 | Consumer loop mati permanen saat kegagalan storage ganda; health tetap `ready:true` dan submit baru diterima 202 tanpa pernah diproses | tinggi (wedge senyap) | **Root perbaiki 09:09** (`running()`, fail-closed `ready()`/`enqueue`, `run()` berhenti disengaja; suite root 13/13) — diverifikasi |
| 2 | `job_history_full` permanen: cap 1000 baris tanpa eviksi apa pun | tinggi (klif ketersediaan) | **Root perbaiki 09:19** (eviksi TTL 7 hari untuk status terminal + cap 10000 + `capacity()`) — diverifikasi |
| 3 | Submit multipart tanpa batas konkurensi (puncak 3 serentak terukur) dan tanpa timeout baca | sedang (memori/slowloris) | **Root perbaiki 09:19** (`submit_slots` Semaphore(2), acquire 0.2 dtk → 429 `worker_busy`, `read_parts` 30 dtk → 408 `upload_timeout`) — diverifikasi |
| 4 | `wait_for(Semaphore.acquire)` bisa membocorkan permit (timeout mendarat tepat setelah grant) | sedang (429 permanen) | **Root perbaiki 09:22** dengan `acquire_slot` shield+harvest — namun lihat #5 |
| 5 | **Residual**: `task.cancel()` pada jalur timeout `acquire_slot` tetap bisa membocorkan permit bila cancel mendarat SETELAH grant tapi sebelum resume (Python <3.12; asyncio Semaphore belum cancellation-safe, GH-90155) | sedang | **Proposal 0005** (reclaim via `add_done_callback`, tanpa cancel) + reproduksi deterministik |
| 6 | Permission longgar: direktori state pratersedia tidak di-chmod, `worker.lock` default umask | rendah | **Root perbaiki 09:19/09:22** (`root.chmod(0o700)`, `os.chmod(lock,0o600)`) — diverifikasi; sidecar `-wal/-shm` POSIX belum di-chmod (catatan minor) |
| 7 | Observasi minor (tanpa patch): respons `invalid_model_response`/`invalid_vision_result` di-collapse ke `local_vision_unavailable`; `ready()` memanggil `capacity()` + 2 probe jaringan di dalam lock enqueue (latensi submit terburut ~6 dtk); baris status `unknown` tidak ikut eviksi TTL; tidak ada logging sama sekali (debugability); idempotency replay berlaku selama riwayat job belum dieviksi TTL 7 hari | rendah | dicatat untuk root |

## Bukti reproduksi (dijalankan terhadap source saat itu)

- Wedge senyap (pra-09:09): submit 202 → `task.done()==True` dengan exception
  sqlite → health `analyze:true` → job berikut 202 tapi `queued` selamanya →
  429 setelah pending penuh.
- Cap riwayat (pra-09:19): `insert` ke-1001 → `job_history_full 429`, 1000 baris
  mengunci permanen.
- Submit tanpa batas (pra-09:19): 3 POST serentak → puncak konkurensi 3.
- Race permit (pola `wait_for(acquire)`, deterministik dengan acquire yang
  menunda setelah grant): TimeoutError → permit hilang → semaphore mati.
- Residual #5 (deterministik, lihat `test_cancel_variant_still_leaks_repro`):
  varian cancel+harvest tetap kehilangan permit saat cancel mendarat pasca-grant.

## Audit per kategori (versi final 09:22)

- **Auth**: `hmac.compare_digest` terhadap `Bearer <token>` pada seluruh route;
  token wajib ≥32 byte; bind `127.0.0.1`. Loopback-only + private tunnel sesuai
  kontrak. Tidak ada celah yang ditemukan.
- **Hang**: semua koneksi DB `timeout=2`; `local()` dibatasi client-timeout
  (total 100s, connect 2s, sock_read 95s) + cap body 256 KiB; `read_parts`
  dibungkus `wait_for(30)`; acquire slot `wait_for(.2)`; loop konsumer berhenti
  fail-closed; `shutdown_timeout` 5 dtk. Tidak ditemukan jalur hang tanpa batas.
- **Memori/upload**: metadata ≤64 KiB; 1–3 gambar ≤10 MiB (cek dimensi ≤4096²/
  16MP, non-animasi, verify+load penuh dengan penjaga DecompressionBomb PIL);
  `submit_slots(2)` membatasi parsing serentak (~62 MiB terburuk + base64);
  `client_max_size` tidak berlaku untuk multipart streaming namun batas manual
  per part lengkap. `image_meta` dijalankan via `to_thread`.
- **Idempotency**: fingerprint sha256(digest,prompt,refs); replay job sama
  mengembalikan hasil tersimpan (200); konflik 409; header `Idempotency-Key`
  wajib sama dengan `job_id`; replay tetap hidup setelah konsumer berhenti
  (diverifikasi tes); pasca-eviksi TTL job lama diperlakukan sebagai job baru
  (semantik terdokumentasi di sini, bukan bug).
- **Async SQL**: seluruh akses Journal via `asyncio.to_thread`, koneksi
  per-panggilan, WAL + busy timeout 2 dtk; tidak ada query di event loop.
- **Process lock Windows**: `msvcrt.locking(LK_NBLCK,1)` setelah `seek(0)` pada
  file yang dipastikan berisi 1 byte; POSIX memakai `flock` non-blokir; instance
  kedua ditolak 409 (diverifikasi suite root); rilis via `close()`.
- **External requests**: satu-satunya endpoint eksternal handler adalah
  `http://127.0.0.1:11439` (model lokal); `trust_env=False`, `allow_redirects=False`,
  `DummyCookieJar`; tidak ada URL dari input; grep `https://` nihil pada worker.
- **router.py**: tidak ditemukan bug. Agregasi health benar (`analyze`/`generate`
  per adapter), routing by `kind`, `close()` menutup keduanya, start kompensasi
  rollback. `import asyncio` lokal di metode — kosmetik.

## Proposal & tes

- `proposals/0005-rtx-vision-submit-slot-late-grant-reclaim.patch` — mengganti
  cancel+harvest dengan `add_done_callback(reclaim)`; permit telat otomatis
  dikembalikan tanpa mustahil bocor; berlaku untuk jalur timeout dan pembatalan
  handler. Berbasis `worker.py 9e817bd7…`; berlaku bersih; suite root 13/13 lulus
  pada scratch ter-patch.
- Proposal 0002/0003/0004 dari putaran pertama **ditarik** — diperbaiki root
  secara paralel (09:09/09:19) dengan semantik setara atau lebih baik
  (mis. upload timeout yang tidak saya usulkan).
- `tests/test_rtx_vision_repro.py` — 12/12 lulus (venv lokal aiohttp 3.12.15/
  Pillow 11.3.0 pada Python 3.9.6; requirements produksi tetap aiohttp 3.14.5/
  Pillow 13 — jalur yang diuji identik API). Cakupan: eviksi TTL + cap 10000,
  gerbang `capacity()` (503), acquire tanpa bocor + repro pola bocor, tolak
  upload ketiga yang lambat (429) lalu slot pulih, permission 0700/0600,
  replay tahan konsumer mati + job baru gagal-tutup (503), upload timeout
  terpasang, hanya endpoint loopback, agregasi router.
- `tests/test_state_store_wal_diagnostic.py` — 9/9 lulus untuk proposal
  `0001-state-store-readonly-wal-diagnostic.patch` (v2) sesuai kriteria
  penerimaan root: salinan dibatasi `MAX_DIAGNOSTIC_COPY_BYTES` (64 MiB),
  tolak symlink/hardlink/owner-mismatch, statistik (size,mtime_ns) stabil
  pra-pasca salin, `-shm` tidak pernah disalin (dan fallback tidak jalan bila
  `-shm` ada), serta `resume`/`checkpoint` menolak snapshot diagnostik
  (`diagnostic_snapshot_nonauthoritative`); listing/status tetap boleh dengan
  tanda `diagnostic/nonauthoritative`.
- Suite WAL helper sebelumnya: `tests/test_sqlite_maintenance.py` 20/20 lulus
  ulang (mohon dijalankan root juga di Linux — perilaku alias `/private` hanya
  macOS; di Linux tidak terpicu).

## Batas

- Tidak menjalankan tes clipboard; tidak membaca history/key pengguna; tidak
  menyentuh DB sesi asli; tidak ada klaim bahwa root memindahkan/mengubah path
  data RTX — `/var/lib/harmonika-rtx` yang basi (non-service) menghilang selama
  jendela audit; data layanan aktif berada di `harmonika-rtx-queue` sesuai unit
  systemd sejak awal.
- Root sedang mengunduh model/runtime di Windows; audit statis + fixture lokal
  saja, tidak ada panggilan ke worker Windows.
