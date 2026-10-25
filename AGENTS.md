# Harmonika Terminal

- Pertahankan binary bermerek pengguna dan cadangan aslinya; jangan patch byte binary.
- Jangan hentikan atau attach writer sesi pengguna. Uji dengan host/socket dan
  data-dir terpisah. Jangan sentuh proyek ERP atau layanan finansial/CS.
- Semua update manual, verified, atomic dan dapat di-rollback. Tidak ada auto-update.
- Model/API publik hanya chat.harmonika.id; dokumentasi produk dapat memakai
  hai.harmonika.id. Referensi/lisensi upstream tetap jujur dan dipertahankan.
- Skill tidak boleh mengklaim tool/provider/dependensi yang belum diuji tersedia.
- Root Codex mengintegrasikan dan menguji. Worker hanya mengubah scope tugasnya.
- Jangan masukkan credential/session/private account ke paket atau log.

## Filosofi core mandiri

- Ikuti [filosofi arsitektur](docs/ARCHITECTURE-PHILOSOPHY.md): AI eksternal adalah
  adapter mentor yang dapat diganti; core dan kontrak domain dimiliki Harmonika.
- TUI hanya memakai gateway/interface internal terversi. SDK, key, endpoint dan
  model vendor tetap di adapter; engine baru tidak mengubah frontend.
- Target media/mockup/vision adalah worker Windows RTX melalui endpoint privat
  terdaftar. Verifikasi readiness/model, autentikasi, batas payload, deadline,
  job/idempotency dan recovery sebelum menyatakan jalur aktif.
- Saat RTX offline, gunakan fallback yang diizinkan dan setara kemampuannya atau
  status degraded. Jangan mengarang analisis atau menganggap GPU/model selalu siap.
- Mentor inference tidak berarti training otomatis; jangan menggabungkan history
  pengguna atau menyimpan dataset pelatihan tanpa rancangan dan otorisasi terpisah.
