# Audit web builder Harmonika

Codex: core privat, gateway, antrean, ownership, approval, sandbox, workflow,
CLI/TUI dan review akhir. OpenCode: analisis/arsitektur/desain, generator
framework/frontend, generator backend/database, serta audit keamanan independen.
Claude tidak dilibatkan.

## Bukti pengujian

Pengujian memakai proyek sintetis terisolasi. Seluruh 161 ID diperiksa melalui
API staging: 155 selesai sesuai mode operasi; 6 koneksi eksternal menolak
akses karena target Notion/SSH belum dikonfigurasi. Hasil akhir produksi dan
jumlah suite disimpan di `WEB-BUILDER-TEST.json` setelah aktivasi.

Aplikasi referensi FastAPI+SQLite menjalankan CRUD, autentikasi sesi, CSRF,
logout, dan browser desktop/mobile. Screenshot aktual ada di
`docs/mockups/web-builder/`. Pengujian browser memakai Chromium software tanpa
GPU. Batas container: network none, non-root, rootfs read-only, tanpa host bind,
CPU 1, RAM 1 GiB, 256 PID; tmpfs workspace 256 MiB dan scratch 128 MiB.
Source yang dapat diekspor tetap dibatasi 256 file / 16 MiB / 512 KiB per file.

## Batas yang harus dibaca

- Analisis dan template deterministik bukan LLM yang menjamin aplikasi bebas
  selesai dari satu prompt. Hasil workflow meminta review kebutuhan dan QA.
- `run_checks` pada workflow menjalankan pemeriksaan sintaks nyata; pengujian
  framework/bisnis harus dijalankan sebagai job QA terpisah.
- 10 scaffold tersedia; hasil runtime teruji utama FastAPI+SQLite dan Next.js.
  MySQL/PostgreSQL, Django, Laravel, Nuxt dan lainnya tidak diklaim telah
  menjalani deployment/beban produksi penuh.
- SSH mengharuskan akun terbatas dan known_hosts milik operator. Tunnel dan
  setup server menghasilkan resep; tidak mengubah firewall/host otomatis.
- Notion memerlukan page allowlist dan credential operator. Deployment nyata
  tersedia melalui adapter Vercel terdaftar, memerlukan izin biaya dan approval;
  production memerlukan izin tambahan. Tanpa konfigurasi, tetap terkunci.
- ID deploy/monitor/maintenance menghasilkan konfigurasi yang dapat diperiksa;
  bukan bukti layanan eksternal atau jadwal sudah dipasang.
- Stop lokal tidak menjanjikan pembatalan transaksi/build remote yang sudah
  diterima. Status submission/READY dibedakan dan tidak di-retry membabi buta.
- Template autentikasi/payment/upload menolak operasi tanpa secret operator.
  OAuth memakai dua cookie __Host- terpisah, state HMAC bertimestamp dan PKCE;
  token exchange tidak diklaim sebagai login sebelum userinfo/session dibuat.
  Webhook pembayaran tidak menandai paid, termasuk ketika flag trust lama aktif,
  sebelum rekonsiliasi gateway diimplementasikan. ID event dipisahkan per provider
  dan perubahan status Midtrans tidak dibuang sebagai duplikat order.
- Tidak ada perubahan layanan pelanggan, ERP, GPU/DRM, atau hms-sstp.
