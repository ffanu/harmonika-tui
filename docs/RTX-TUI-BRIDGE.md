# Bridge gambar Harmonika TUI

Aktif pada backend `chat.harmonika.id` dan helper lokal bertanda tangan
`2026.10.25.31`. Binary TUI tetap sama, pembaruan otomatis tetap nonaktif.

## Jalur eksekusi

`generate_image` MCP → client Node async → HTTPS `/v1/media` → autentikasi dan
kuota API gateway → principal pemilik → antrean SQLite privat → adapter HTTP
async → tunnel privat → worker Windows RTX. TUI tidak menerima credential
worker atau akses langsung ke PC GPU. Antrean memproses satu job GPU sekaligus;
chat dan event loop tidak menunggu secara blocking.

| Endpoint | Fungsi |
|---|---|
| `GET /v1/media/capabilities` | Readiness aktual worker, analisis, referensi, antrean |
| `POST /v1/media/jobs` | Submit async; wajib `Idempotency-Key` |
| `GET /v1/media/jobs/{job_id}` | Poll status; Bearer pemilik |
| `POST /v1/media/jobs/{job_id}/cancel` | Minta pembatalan lokal |
| `POST /v1/media/jobs/{job_id}/recover` | Recovery jika worker mendukung |
| `POST /v1/media/files` | Upload satu `file` multipart privat |
| `GET /v1/media/files/{file_id}` | Unduh privat, Bearer pemilik |

Body job:

```json
{"kind":"generate","prompt":"Satu lingkaran jingga di latar biru","size":"512x512","reference_file_ids":[]}
```

Respons submit `202` membawa `job.job_id` dan `job.status`. Status:
`queued`, `dispatching`, `running`, `completed`, `failed`, `cancel_requested`,
`cancelled`, `unknown`. Hasil lengkap ada di `job.result.artifacts[]` dengan
`file_id`, `mime_type`, `size_bytes`, `width`, `height`, `sha256`. Tidak ada URL
publik. API ini polling, bukan SSE baru; SSE percakapan tetap terpisah.

## Guard dan batas

- Caller memakai lisensi API Harmonika biasa. Gateway mengambil identitas token
  dari DB, bukan dari body/header pemilik yang dikirim client. Foreign ID →404.
- Secret bridge hanya di server0600 dan systemd credential; tidak ada key dalam
  repo, argv, log prompt, atau respons tool. Gateway tidak meneruskan key caller
  ke worker. Kuota token yang sudah ada tetap berlaku.
- Maksimum input gambar10MiB, hasil16MiB, tiga referensi. Worker nyata saat ini
  **generate=true, analyze=false, references=false**. Jangan menganggap upload
  berarti model mampu vision atau image editing.
- Timeout koneksi worker3s, job120s, tiap request client20s, polling150s.
  Status worker/capability dicek sebelum submit. Ketika offline/tidak mendukung
  dan belum ada dispatch, MCP boleh memakai jalur Harmonika RPC yang sudah ada.
- POST tidak diulang otomatis. Idempotency generate stabil untuk target+prompt.
  Setelah accepted/timeout ambigu, tidak pindah engine diam-diam atau membuat
  job kedua; kembalikan status tidak pasti dan periksa job terlebih dahulu.
- Worker legacy belum mendukung cancel/recover remote. Cancel menahan hasil
  agar tidak dipakai, bukan klaim menghentikan GPU seketika. Job `unknown`
  menahan dispatch berikutnya sampai diverifikasi; jangan hapus lock otomatis.
- Hasil diverifikasi ukuran, MIME, hash dan signature PNG sebelum ditulis
  `wx`,0600 dalam root proyek. File lama tidak ditimpa, Base64 tidak masuk
  konteks hasil MCP.

## Pengujian dan aktivasi

-13 tes client async,33 tes MCP/branding,24 tes antrean Linux, dan tes Go gateway
  lulus. Skenario mencakup offline, timeout ambigu, GET polling, hash salah,
  pemilik berbeda, upload privat, fallback sebelum dispatch, dan guard output.
- Smoke HTTPS memakai dua token uji sementara: accepted async, retry idempotency
  tetap satu job, foreign job/file ditolak, PNG512×512 valid. Token/quota/job/file
  fixture dibersihkan; sampel render disimpan privat sebagai bukti QA.
- Smoke stdio dari helper terpasang →Windows GPU selesai6.926s, hasil507414byte,
 0600, tidak menyisipkan image Base64 ke hasil tool. Ini tes jalur tool nyata,
  bukan simulasi penekanan tombol user di TUI.
- Hanya `harmonika-rtx-queue` dan `harmonika-web-chat-live` direstart untuk bridge.
  Sepuluh service terkait lain tetap PID sama. Renderer/tunnel dan GPU/DRM tidak
  diubah. Backup rollout berada di `/var/backups/harmonika-media/20261025T012145Z`.
- MCP pemilik mengaktifkan `HARMONIKA_MEDIA_BRIDGE=1`. Proses MCP yang sudah
  berjalan mengambil implementasi baru ketika di-reload/dibuka oleh pengguna;
  sesi TUI pengguna tidak dimatikan otomatis.

## Batas kesiapan

Analisis mockup via adapter v1 sudah diuji dengan fixture dan kontrak `kind=analyze`
+ referensi privat. Namun worker Windows yang tersambung masih legacy generate
saja. `analyze_image` memakai pipeline Harmonika sebelumnya saat capability GPU
false. Aktivasi vision RTX nyata perlu model vision dan endpoint worker v1 di
PC Windows, kemudian smoke visual nyata sebelum capability diaktifkan.

## Perbaikan transport terminal — 2026.10.25.34

MCP memakai API media berlisensi secara default; tidak lagi membutuhkan
`HARMONIKA_MEDIA_BRIDGE=1` dari environment TUI. Nilai eksplisit `0` tetap
menonaktifkan bridge untuk pengujian/jalur lama yang disengaja. Key privat tetap
wajib dan tidak ada credential SSH yang ditambahkan ke instalasi publik.
Sesi MCP yang sudah berjalan perlu dimuat ulang oleh pengguna.

Verifikasi: 47 tes media/MCP/portable lulus, ditambah tes regresi default
HTTPS tanpa flag. Smoke stdio MCP rilis terpasang tanpa flag menghasilkan PNG
501.209 byte dalam 15,214 detik melalui HTTPS → antrean privat → Windows GPU.
File pengujian bersifat sementara dan dibersihkan; file pengguna tidak diubah.

## Perbaikan pemeriksaan readiness — 2026.10.25.35

Tool MCP `capabilities` kini memakai transport media HTTPS yang sama dengan
`generate_image` ketika bridge aktif. Sebelumnya status memakai RPC SSH lama dan
dapat melaporkan `backend_unavailable` walaupun API media siap. Respons hanya
menampilkan capability yang dikembalikan API; tidak menyatakan semua tool lama
tersedia. Kegagalan probe tetap degraded dan tidak dialihkan ke SSH.
50 tes media/MCP/portable lulus. Smoke terminal memeriksa readiness terlebih
dahulu sebelum membuat PNG, tanpa environment flag tambahan.
