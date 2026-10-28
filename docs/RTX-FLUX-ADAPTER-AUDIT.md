# Audit Read-Only: Upgrade Generator RTX sd-turbo → FLUX.2 Klein 4B

- **Tanggal:** 2026-10-25
- **Mode:** Audit read-only. Tidak ada file repo/production yang diubah, tidak ada proses dihentikan, tidak ada secret dibaca/log.
- **Sumber audit:**
  - Repo lokal: `/Users/id/Projects/harmonika-tui` (README.md, AGENTS.md, `backend/rtx_queue/*`)
  - VPS `harmoni` via ssh: `/opt/harmonika-ai/lib/rtx-image-router.js`, `/opt/harmonika-ai/rtx-queue/backend/rtx_queue/{adapter.py,core.py,service.py,deploy/config.example.json}`, `/opt/harmonika-ai/api/server.js`, `/opt/harmonika-ai/crush-bridge/worker.py`, `/opt/harmonika-ai/lib/rtx-image-router.test.js`
  - Probe live read-only: `GET /health` pada `127.0.0.1:18182` (worker) dan `127.0.0.1:18186` (queue, hanya verifikasi auth menolak token invalid).
- **Catatan path:** File yang diminta `backend/rtx_queue/adapters.py` secara aktual adalah `adapter.py` (satu file, class `RTXAdapter`). MD5 adapter.py/core.py VPS **identik** dengan repo lokal; hanya `service.py` yang berbeda (lihat §4).

## 1. Topologi jalur generate saat ini

```
api/server.js (runRTXFirstRouter, fallback runCodexImageTool)  ──┐
crush-bridge/worker.py (generate_image, pin model)             ──┼──> 127.0.0.1:18182 (SSH tunnel → Windows RTX worker legacy)
rtx-queue service :18186 (SQLite queue → RTXAdapter legacy)   ──┘
```

- `18182` = SSH forward (pid ssh) ke worker Windows; `18186` = `harmonika-rtx-queue.service` (active).
- Worker live terkonfirmasi (GET /health):
  `{"ok":true,"model":"stabilityai/sd-turbo","device":"cuda","cuda":true,"gpu":"NVIDIA GeForce RTX 5070","loaded":true,"output":"C:\\Users\\<redacted>\\Pictures\\HarmonikaAI"}`
- Queue `18186` menolak Bearer invalid dengan `{"error":{"code":"invalid_token"}}` (auth aktif, sesuai kontrak README).

## 2. Kontrak worker lama (18182) — inventaris per konsumen

### 2.1 GET /health

| Konsumen | Validasi | Timeout |
|---|---|---|
| `lib/rtx-image-router.js` | `ok===true && cuda===true && device==='cuda' && typeof model==='string'` (model **tidak** dip-pin ke nilai tertentu) | 3000 ms |
| `rtx_queue/adapter.py` (legacy) | `ok is True && cuda is True && device == 'cuda'` (model tidak diperiksa) | 3 s (`connect_timeout`) |
| `crush-bridge/worker.py` | `cuda is True && device == 'cuda' && model == 'stabilityai/sd-turbo'` ← **PIN MODEL HARD-CODE** | 2 s |

### 2.2 POST /generate — request

| Konsumen | Payload | Timeout |
|---|---|---|
| rtx-image-router.js | `{prompt(≤4000 chars, trim), width:768, height:768, steps:4, guidance_scale:0.0}` hard-coded | 90 s total (`renderTimeoutMs`), redirect dilarang, Content-Type wajib JSON |
| adapter.py `_legacy` | `{prompt, width, height}` dari whitelist size `{'512x512','768x768','1024x1024'}` + `steps:4, guidance_scale:0.0` | total 120 s (`job_timeout`), connect 3 s, **sock_read 15 s** |
| crush-bridge | `{prompt(≤3000), width:512, height:512, steps:1, guidance_scale:0.0}` | 45 s |

### 2.3 POST /generate — response (validasi ketat, semua konsumen)

- `rtx-image-router.js`: `ok===true && device==='cuda' && model===health.model (identik) && b64_png` match `^[A-Za-z0-9+/]+={0,2}$` dan `length % 4 === 0`; body JSON ≤ 24 MiB. Lalu PNG divalidasi penuh: magic, CRC per chunk, IHDR pertama, **≤2048×2048, depth 8, color type 2 (RGB) / 6 (RGBA), non-interlaced, filter ≤4**, ukuran ≤16 MiB, panjang inflate IDAT eksak, IEND di akhir.
- `adapter.py`: `ok is True && device=='cuda' && b64_png` (model tidak diperiksa), base64 strict, hasil decode ≤16 MiB (`RESULT_LIMIT`); response JSON ≤24 MiB.
- `crush-bridge`: `ok===true && model===health.model && device==='cuda'`, PNG persis **512×512**.

### 2.4 Timeouts & cooldown tambahan (router JS)

- Single-flight `active` (satu render pada satu waktu per proses API).
- Setelah render gagal setelah POST terkirim: cooldown **300 s** (5 menit) — hasil terlambat worker tidak pernah dipublikasikan. Gagal sebelum POST (health/busy): cooldown 30 s.
- Alasan fallback: `offline | busy | cooldown | reference_unsupported | worker_failed`.

### 2.5 Queue serialization (rtx-queue, `core.py`)

- 1 dispatch remote aktif; 64 pending global; 8 pending/principal; claim SQLite transaksional + flock.
- Antre menunggu maks 15 menit (deadline → `queue_deadline_exceeded`).
- Timeout setelah POST = status **unknown** (bukan retry buta); slot ditahan; health menampilkan `blocked_by_uncertain_job=true`, `queue_ready=false`. Recovery GET-only hanya mode v1; **worker legacy tidak punya recovery** (butuh verifikasi operator).
- Startup service: in-flight → `unknown`. Idempotency per (owner, Idempotency-Key); payload beda + key sama → 409.

### 2.6 Fallback Codex

- `api/server.js:81` — `createRTXFirstRouter({fallback: runCodexImageTool, ...})`; `runCodexImageTool` (server.js:425) menerima prompt ≤4000 + references, menulis ke tmpdir. Fallback dipakai saat: worker offline/bussy/cooldown/gagal render/references ada/PNG invalid/storage gagal (storage gagal ≠ fallback, langsung error).
- `crush-bridge/worker.py:177` — exception apa pun di jalur 18182 → `ENGINE /v1/harmonika/media/generate` (jalur router di atas), budget timeout sisa (`220 s − elapsed`).
- **rtx-queue TIDAK punya fallback Codex** — worker offline = job `failed/rtx_offline`. Switch model yang gagal akan terlihat sebagai kegagalan job keras di jalur queue, bukan fallback.

### 2.7 Batas 1024 / image refs

- Whitelist ukuran generate: `{'512x512','768x768','1024x1024'}` — **maksimum 1024×1024** (`core.py submit`, error `invalid_size` di luar itu).
- `reference_file_ids`: maks **3**, unik, wajib milik owner; `analyze` wajib ≥1 referensi.
- Worker legacy melaporkan `analyze=false, references=false` (adapter): job analyze → `vision_unavailable`; generate dengan refs → `references_unavailable`. Di jalur router JS, `references.length>0` langsung fallback Codex (`reference_unsupported`).
- Input file queue: PNG/JPEG/WebP statis, ≤10 MiB, ≤4096×4096 / ≤16.777.216 px, animasi ditolak. Output ≤16 MiB/gambar. Prompt ≤12.000 byte; JSON job ≤64 KiB. Storage 1 GiB/1000 file; 10.000 job history.

## 3. Temuan penting (gap & risiko kontrak)

1. **PIN MODEL di crush-bridge** (`worker.py:161`): `model != "stabilityai/sd-turbo"` → `rtx_unavailable` → jalur RTX crush-bridge **mati permanen & senyap** (selalu fallback ke ENGINE/Codex) begitu model berganti. Ini satu-satunya tempat di VPS yang mem-pin identitas model.
2. **`sock_read=15 s` di adapter.py:18** (jalur queue): POST /generate legacy bersifat sinkron — worker menghitung dulu baru mengirim body. Jika FLUX.2 Klein butuh >15 detik tanpa satu byte pun terkirim, aiohttp memutus (`rtx_timeout_or_connection_lost`, uncertain) → job `unknown` → **slot queue terkunci + `queue_ready=false` sampai intervensi operator** (legacy tidak punya recovery). sd-turbo sekarang ~0,82 s sehingga tidak pernah kena; FLUX hampir pasti menembus 15 s pada 1024.
3. **Budget latency per konsumen berbeda**: 45 s (crush-bridge, 512) < 90 s (router, 768) < 120 s (queue, ≤1024). Batas paling ketat adalah crush-bridge 45 s pada 512.
4. **Identity check `model === health.model`** (router & crush-bridge): worker baru harus melaporkan string model yang **stabil identik** di /health dan /generate (termasuk saat reload/swap model internal).
5. **PNG constraints ketat di router**: wajib 8-bit, RGB/RGBA, non-interlaced, ≤2048×2048, ≤16 MiB, CRC benar. Pipeline post-process FLUX (mis. output float/16-bit atau RGBA dengan metadata aneh) harus dinormalisasi sebelum encode PNG.
6. **Drift repo vs VPS**: `service.py` repo lokal punya blok `vision_endpoint` + `WorkerRouter` (router.py) yang **belum ada di VPS**. Tidak mengganggu switch ini, tapi upgrade/deploy berikutnya harus sadar drift (root Codex yang mengintegrasikan).
7. Fixture tes `rtx-image-router.test.js` memakai model string sd-turbo, tapi **tidak ada assertion yang mem-pin nilai** — hanya konsistensi health/generate; tes tetap hijau tanpa perubahan.
8. Minor hygiene: /health mengekspos path Windows (berisi username) — loopback-only, risiko rendah; tidak ada credential yang bocor.
9. Tunnel 18182 adalah SSH forward ke port Windows tetap — **tidak perlu disentuh** jika worker baru mendengarkan di port Windows yang sama.

## 4. Rencana perubahan minimal (worker baru tetap kontrak lama)

Prinsip: **semua perubahan perilaku di sisi worker Windows (Codex); VPS cukup 1 file.** Router JS, adapter.py, core.py, service.py VPS, tunnel, dan queue service tidak diubah.

### A. Worker baru FLUX.2 Klein 4B (Windows RTX 5070, dikelola Codex)

1. `GET /health` → pertahankan bentuk: `{"ok":true,"model":"<id-baru>","device":"cuda","cuda":true,...}`. Key lama wajib ada; key tambahan (mis. `loaded`, `gpu`) diabaikan konsumen — aman. `ok` hanya `true` setelah model selesai dimuat (jaga `loaded` semantics; hindari menerima /generate saat cold).
2. `POST /generate` → **terima field lama apa adanya**: `prompt, width, height, steps, guidance_scale`. `steps`/`guidance_scale` boleh diabaikan/dipetakan internal (FLUX.2 Klein distilled; jangan 4xx karena field ini). Dukung 512/768/1024 persegi (dan angka lain dari hard-coded konsumen: 512, 768).
3. Response → `{"ok":true,"model":"<sama persis dengan /health>","device":"cuda","b64_png":"<base64 standar>"}`. PNG: 8-bit, RGB (flatten) atau RGBA, non-interlaced, ≤2048×2048, ≤16 MiB.
4. Budget latency (target internal worker, verifikasi oleh Codex di PC):
   - /health < 2 s (batas crush-bridge).
   - 512×512 ≤ 40 s; 768×768 ≤ 85 s; 1024×1024 ≤ 115 s (margin di bawah timeout konsumen).
   - **PENTING (jalur queue):** pastikan byte pertama respons < 15 s setelah request (kirim header HTTP lebih awal / chunked), ATAU selesaikan render < 15 s — karena `sock_read=15 s` adapter. Jika tidak memungkinkan, angkat sebagai keputusan terpisah untuk menaikkan `sock_read` di adapter.py (perubahan VPS ke-2, perlu re-deploy queue service — bukan bagian rencana minimal).
5. Keep-alive & error: jangan tutup koneksi paksa di tengah render; error tetap JSON (bukan HTML), agar dipetakan `worker_unavailable`/`invalid_result` dengan benar.
6. Listen di **port Windows yang sama** dengan worker lama agar tunnel 18182 tidak perlu diubah; jalankan paralel di port lain dulu untuk pengujian (lihat §6).

### B. VPS — satu-satunya perubahan wajib

- `crush-bridge/worker.py:161`: ganti pin `model == 'stabilityai/sd-turbo'` → model ID baru (atau allowlist kecil yang configurable). Tanpa ini jalur RTX crush-bridge degrade permanen ke Codex.

### C. Opsional (non-blocking, disarankan)

- Update fixture model string di `lib/rtx-image-router.test.js` agar dokumentasi tes mencerminkan realitas (tidak ada assertion yang bergantung nilai).
- Tidak mengubah `rtx-image-router.js` (tidak mem-pin model) dan `adapter.py` (tidak memeriksa model pada legacy).

### D. Rollback

- Rollback = sisi PC saja: worker lama sd-turbo start kembali di port yang sama + revert pin crush-bridge. Router/queue tidak berubah sehingga rollback tidak menyentuh kontrak lain. (Sesuai AGENTS: manual, verified, atomic, dapat di-rollback.)

## 5. Uji regresi yang perlu dijalankan

1. **Unit router (VPS):** `node --test /opt/harmonika-ai/lib/rtx-image-router.test.js` — musti hijau tanpa perubahan (fixture konsisten health/generate; CPU/corrupt-PNG/wrong-device → fallback Codex tetap teruji).
2. **Unit queue (repo/VPS venv):** `python -m unittest discover -s tests/rtx -v` (20 tes: offline, timeout ambigu, singleton, transaksi, cancel, recovery GET-only, auth/owner, idempotency, gambar corrupt, field invalid, file privat). Kode tidak berubah → harus tetap hijau sebagai baseline.
3. **Kontrak worker baru (di PC oleh Codex, dari VPS via tunnel uji):**
   - /health: shape, `model` string non-kosong dan identik dengan /generate; latensi < 2 s.
   - /generate matrix: {512,768,1024} × prompt pendek/4000-char × 2x berurutan (identitas `model` konsisten); PNG lulus validator (depth 8, RGB/RGBA, non-interlace, CRC, ≤16 MiB, dimensi sesuai request).
   - Field legacy: kirim `steps:1`, `steps:4`, `guidance_scale:0.0` → tidak boleh 4xx.
   - Timing per ukuran terhadap budget §4.A.4, khususnya **first-byte < 15 s**.
4. **E2E queue:** `deploy/smoke.py` (server-side, job generate nyata via 18186) → status completed, artifact valid, idempotent re-request tidak render ulang.
5. **E2E router/API:** satu generate via jalur `api/server.js` → manifest `provider:'rtx-local'` + `model` baru di `/fast/output/media/*/manifest.json`; lalu simulasi worker gagal (PC stop sementara di jam maintenance) → fallback Codex aktif (`image_route provider:codex`) dan cooldown 300 s bekerja.
6. **crush-bridge:** `generate_image` via RTX (bukan fallback) setelah pin diupdate — verifikasi manifest `provider=='rtx-local'` pada jalur ENGINE, dan jalur 512×512 + timeout 45 s terpenuhi.
7. **Negative:** queue analyze job → tetap `vision_unavailable`; generate + refs → `references_unavailable`; ukuran di luar whitelist → `invalid_size` (kontrak tidak bergeser).

## 6. Risiko switch (dengan mitigasi)

| # | Risiko | Dampak | Mitigasi |
|---|---|---|---|
| 1 | Render > 15 s first-byte pada jalur queue (sock_read adapter) | Job `unknown`, slot terkunci, `queue_ready=false`, perlu operator | Worker kirim header early/chunked ATAU selesaikan <15 s; uji khusus sebelum switch; fallback: naikkan `sock_read` sebagai perubahan terpisah |
| 2 | Latency > 45 s @512 (crush-bridge) / > 90 s @768 (router) | Fallback Codex (biaya/latensi naik), cooldown 5 menit router | Profil latency FLUX di PC dulu; kalau 512 > 40 s, sepakati penyesuaian timeout crush-bridge (perubahan terpisah) |
| 3 | 1024×1024 + 12 GB VRAM → OOM/slow | Job queue gagal keras (tanpa fallback) | Uji beban 1024 berulang di PC; jika tidak stabil, tetap pertahankan whitelist (kontrak) tapi blokir 1024 di worker dengan error JSON jelas, dan evalusi kebijakan size terpisah |
| 4 | `model` health ≠ generate (reload/hot-swap) | `invalid_result` → fallback loop | String model deterministik per proses worker; reload = proses baru |
| 5 | PNG FLUX tidak lolos validator router (16-bit/interlace/alpha aneh) | Dianggap corrupt → fallback, file tidak dipublikasikan | Normalisasi ke 8-bit RGB non-interlace sebelum encode; uji dengan validator `validatePNG` router |
| 6 | Pin crush-bridge terlambat diupdate | Degrade senyap ke Codex (tidak error, jadi tidak terdeteksi) | Ganti pin pada saat yang sama dengan switch; monitor log `image_route provider:codex reason:*` |
| 7 | Cold start model (health ok=true sebelum loaded) | /generate timeout → cooldown | Worker wajib `ok:false` (atau tidak listen) sampai loaded; pertahankan semantics `loaded` |
| 8 | Drift `service.py` repo vs VPS (WorkerRouter vision) tertukar saat deploy manual | Fitur vision v1 belum diuji di VPS bisa ikut terdeploy | Deploy hanya crush-bridge pin; jangan sync service.py dalam scope ini; drift ditangani root Codex |
| 9 | `guidance_scale/steps` ditolak worker baru | 4xx → `worker_http_error`/`worker_unavailable` di semua jalur | Regression test field legacy (§5.3) wajib sebelum switch |

## 7. Rekomendasi konkrit

1. **Urutan switch (maintenance terjadwal, operator/Codex):**
   a. Codex pasang worker FLUX.2 Klein 4B di PC pada **port uji** (mis. 18183 Windows), model ID final ditetapkan (mis. `black-forest-labs/FLUX.2-klein-4b` — konfirmasi ID persisnya dari artefak instalasi, jangan tebak).
   b. Dari VPS, probe read-only via SSH forward sementara ke port uji: health < 2 s, matrix ukuran + first-byte < 15 s, PNG lulus `validatePNG`.
   c. Update pin model `crush-bridge/worker.py` (satu-satunya edit VPS) + jalankan ulang unit crush-bridge bila ada.
   d. Stop worker lama & start worker baru **di port Windows yang sama** (aksinya di PC, bukan di VPS; tunnel 18182 dan semua service VPS tidak disentuh).
   e. E2E: `deploy/smoke.py`, satu generate via API (manifest provider `rtx-local`), satu generate_image crush-bridge; lalu negative tests §5.7.
   f. Pantau log `image_route` 1×24 jam pertama; alarm manual bila `provider:codex` dominan padahal PC online.
2. **Jangan sentuh** `rtx-image-router.js`, `adapter.py`, `core.py`, `service.py` VPS, tunnel SSH 18182, dan `harmonika-rtx-queue.service` dalam scope ini.
3. **Keputusan yang perlu dieksplicitkan sebelum switch** (bila FLUX > 15 s): terima risiko unknown-job pada queue, ATAU naikkan `sock_read` adapter (perubahan kecil tapi re-deploy queue — keluarkan dari rencana minimal dan uji 20 tes queue ulang).
4. **References/analyze tetap tidak di klaim**: FLUX.2 Klein adalah T2I distilled; jangan aktifkan `analyze/references` sampai worker vision teruji (konsisten AGENTS: jangan klaim kapabilitas yang belum diuji). Jalur refs tetap fallback Codex seperti hari ini.
5. **Dokumentasikan** model ID baru di `backend/rtx_queue/README.md` bagian worker legacy (catatan: adapter legacy tidak memeriksa model; hanya crush-bridge yang mem-pin) — update docs oleh root Codex saat integrasi, bukan bagian audit ini.
6. **Rollback plan tertulis** sebelum switch: worker lama sd-turbo dipertahankan siap start di PC (binary/env tidak dihapus), revert pin crush-bridge, verifikasi `image_route provider:rtx` kembali.

---
*Audit dilakukan tanpa membaca/menyimpan secret (token/key tidak diakses; path username di respons health diredaksi). Tidak ada mutasi repo/production dan tidak ada proses dihentikan.*
