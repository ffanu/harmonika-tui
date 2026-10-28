# Dual-PC: lifecycle dan Data Firewall

Status: aktif, 25 Oktober 2026. Helper TUI: **2026.10.25.38**.

## Alur

PC 1 Linux: gateway → registry/antrean async → HTTP privat melalui tunnel SSH → PC 2 Windows RTX: worker → ComfyUI. Browser/TUI hanya mengakses gateway berautentikasi. ComfyUI tetap loopback; tidak dibuka ke internet dengan `0.0.0.0`.

**Use → Log → Correct → Test → Activate:** pemanggilan skill memakai registry; kegagalan diisolasi dan dicatat; perbaikan kode diperiksa Codex/OpenCode; fault injection dilakukan pada server sementara; aktivasi dilakukan setelah tes dan pemeriksaan kesehatan. Ini bukan pelatihan otomatis dari percakapan atau mekanisme AI mengubah kode production sendiri.

## Log mekanis, tanpa data pelanggan

Tabel native SQLite `skill_failures` tepat empat kolom:

| Kolom | Nilai |
|---|---|
| timestamp | UTC ISO-8601 |
| skill_id | ID skill terdaftar, misalnya `image.generate` |
| error_type | Enum teknis tetap, bukan pesan exception |
| latency | Milidetik, finite dan nonnegatif |

Tidak ada prompt, jawaban, riwayat, identitas pelanggan, principal/token hash, nomor telepon, file path, URL, credential, payload, atau stack trace di log baru ini. ID yang tidak dikenal tidak dicatat. Error bebas dipetakan ke enum tetap. Stdout audit gateway hanya HTTP status dan elapsed_ms. Diagnostik menyeleksi field secara eksplisit sebelum sampai ke TUI.

DB registry: `/var/lib/harmonika-master/discovery.sqlite3` (root). DB antrean: `/var/lib/harmonika-rtx-queue/queue.sqlite3` (user service antrean). Keduanya WAL, 0600, integritas diperiksa. Tidak ada penulisan lintas UID. Ringkasan antrean diambil registry melalui HTTP loopback berautentikasi; credential tetap server-side.

Pembatasan: paling banyak 1000 peristiwa dan pembersihan usia >7 hari **setiap append**. Kegagalan/lock SQLite tidak menggagalkan respons skill; status menampilkan `logging_unavailable` bila ringkasan tidak tersedia. Pemeriksaan menolak symlink/hardlink, owner/mode salah, penggantian inode, sidecar tidak aman, atau tambahan kolom yang tidak sesuai.

Data pemrosesan pekerjaan yang memang diperlukan tetap berada di penyimpanan fungsional privat `jobs`/files. Larangan data pelanggan berlaku pada **log teknis baru**, bukan klaim bahwa seluruh database antrean tidak menyimpan input pekerjaan. Database fungsional dan sesi pengguna tidak diekspor/disalin sebagai log.

## Koneksi dan error

- Probe dibatasi waktu, ukuran JSON dan struktur. HTTP gagal, autentikasi gagal, payload rusak, offline, warming, busy dan VRAM penuh dipetakan ke kode aman.
- Worker Windows mengenali tipe exception OOM tanpa mengirim pesan exception mentah.
- Sibuk/VRAM penuh yang ditolak secara pasti menghasilkan job `failed`; gateway tetap hidup.
- Timeout/putus setelah POST mungkin telah diterima menghasilkan `unknown`/`uncertain`. Jangan kirim ulang otomatis: ini mencegah render ganda. Jalur legacy memerlukan pemeriksaan operator untuk pemulihan status yang tidak pasti.
- TUI menerima error terstruktur: `status: degraded`, kode, pesan aman, `retryable`, `retry_safe`, `operation_state`.
- Pada jalur media TUI ini, kegagalan RTX tidak diam-diam mengirim input ke mentor. Fallback lain hanya boleh dipilih melalui konfigurasi internal eksplisit dan kebijakan data yang sesuai.
- Tidak ada instalasi/aktivasi driver GPU pada PC 1 Linux.

## CLI

```sh
harmonika state status --format text
harmonika state status --offline --format text
```

Online: membaca `GET /v1/skills/discovery` yang sekarang menyertakan `diagnostics.graphics`, `registry_errors`, dan `graphics_errors`. Probe memiliki cache singkat 1 detik, timeout maksimal 4 detik pada agregator, dan timeout CLI 7 detik. Bila banyak pemanggil datang bersamaan, agregator memakai hasil terakhir; bukan membanjiri PC 2.

Offline: menampilkan **Tidak diperiksa (mode offline)**, bukan mengklaim PC 2 sedang mati. Log yang tidak bisa dibaca diberi keterangan belum tersedia lengkap.

Contoh hasil pengujian live:

```text
Graphics Engine PC 2: Online
Error skill terisolasi: 2
  ... | text.metrics | invalid_input | ... ms
  ... | image.generate | graphics_busy | ... ms
```

Dua peristiwa tersebut berasal dari validasi input sintetis dan observasi sibuk selama render uji. Tidak mengandung data percakapan.

## Pengujian dan aktivasi

| Kelompok | Hasil |
|---|---|
| Registry discovery | 107 tes lulus, Linux |
| FailureLog | 21 tes lulus |
| Guard schema dan sidecar | 2 tes lulus |
| WAL/integritas | 4 tes lulus |
| RTX antrean dan isolasi fault | 30 tes lulus (termasuk offline, busy, OOM, payload rusak, timeout, logging gagal) |
| HTTP gateway | 10 tes lulus pada venv service master sebenarnya |
| Diagnostik gateway | 4 tes lulus pada venv service master sebenarnya |
| Windows worker | 12 tes lulus |
| State diagnostik | 4 tes lulus |
| MCP/media bridge | 36 tes lulus |

Tes offline/OOM memakai worker sementara, tanpa mematikan PC pelanggan atau memenuhi VRAM production. Tes PNG nyata: MCP terpasang → HTTPS → antrean → Windows RTX → file PNG, **23,251 detik**, 315663 byte, mode 0600; data gambar base64 tidak masuk konteks tool.

Aktivasi pertama ditolak health guard karena `web.RequestKey` tidak tersedia di aiohttp venv master. Rollback otomatis berhasil. Kode diperbaiki memakai request string keys yang kompatibel, diuji ulang pada venv sebenarnya, kemudian aktivasi berhasil. Tidak ada error service setelah aktivasi akhir.

Service yang direstart: `harmonika-master-skills`, `harmonika-rtx-queue`, serta task Windows milik pipeline `HarmonikaFluxWorker` ketika idle. Node gateway, web gateway, tunnel permanen, layanan lain, binary TUI dan sesi pengguna dipertahankan. Overlay helper ditandatangani Ed25519/SHA-256 dan `harmonika doctor` lulus. Automatic update tetap nonaktif.

## Berkas implementasi

- `backend/engine_registry/failure_log.py` dan `discovery.py`: native SQLite serta isolasi executor (OpenCode, diperiksa/diperkuat Codex).
- `backend/master_engine/diagnostics.py` dan `service.py`: agregasi privat dan audit teredaksi (Codex).
- `backend/rtx_queue/diagnostics.py`, `adapter.py`, `service.py`: taxonomy, timeout, payload guard, pencatatan antrean (Codex).
- `backend/rtx_flux/worker.py`: busy/OOM/health worker Windows (Codex).
- `runtime/tools/state_store.py`, `mcp-core.cjs`: status dan error terstruktur TUI (Codex).
- `scripts/activate-lifecycle-status-helper.py`: overlay helper bertanda tangan, tanpa mengganti binary.

Review OpenCode: `LIFECYCLE-OPENCODE-REVIEW.md`. Snapshot lama OpenCode mencatat kekurangan aiohttp pada Python Mac; pengujian Linux lengkap di atas menutup keterbatasan lingkungan tersebut.
