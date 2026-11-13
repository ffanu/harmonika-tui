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
- Workbench memakai server/socket tmux privat per peluncuran; jangan mengubah
  konfigurasi tmux pengguna atau mengirim signal ke sesi yang sudah berjalan.
- Metrik hanya metadata read-only. Jangan membaca messages.parts/title atau
  membuka log backend mentah demi panel monitoring; jangan menebak token/biaya/ETA.

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

## OCR lokal bersama (diperbarui 2026-11-13)
- Gateway OCR memakai Windows RTX Qwen3-VL 8B sebagai pilihan utama; 2B tersedia
  sesuai VRAM dan PaddleOCR PP-OCRv5 Linux CPU menjadi fallback yang dilaporkan.
- `analyze_image` default `mode=ocr`; `mode=vision` untuk analisis adegan/layout RTX.
- Runtime/worker OCR diawasi startup task dan watchdog. Health harus diuji dengan
  ekstraksi nyata; pertahankan metadata engine aktual/fallback serta pola O/0, I/l/1.
- Pertahankan metadata local_only, untrusted content, dan tanda ketidakpastian OCR.
- OCR tidak membuktikan keaslian ID; analisis percakapan tetap memakai model chat.

## Desain visual dan konteks (2026-11-13)
- Foto/ilustrasi memakai generator RTX; UI/teks presisi diekspor sebagai PNG dari browser, scale 1.
- Periksa piksel dengan mode=vision, teks dengan OCR dan DOM; jangan menyamakan byte PNG dengan kualitas.
- Metadata model wajib berasal dari hasil backend. Jaga pasangan call/result saat mengurangi konteks transport; jangan mengubah riwayat asli atau mengklaim teks yang dihilangkan sebagai bukti eksekusi.
- Worker visual menyimpan observasi di RAM berbatas, SQLite hanya mekanika, tanpa isi gambar/chat.
