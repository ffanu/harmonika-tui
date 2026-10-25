---
name: harmonika-tui-dev
description: Bangun atau perbaiki antarmuka terminal Harmonika dengan layout berbasis sel, resize, fokus keyboard, dan state UI yang teruji.
---

Gunakan untuk layar terminal, composer, riwayat, panel tool, navigasi dan rendering TUI. Baca README, instruksi proyek, Git status, source event loop serta referensi layar sebelum mengedit. Pertahankan stack yang ditemukan; jangan memasang framework TUI baru hanya karena skill ini.

Tentukan alur utama dan kriteria penerimaan: ukuran minimum, panel yang dapat disembunyikan, urutan fokus, tombol submit/cancel, posisi composer, loading/empty/error, dan perilaku resize ketika request berjalan. Simpan catatan checkpoint singkat sesuai konvensi proyek. Untuk perubahan besar, buat referensi yang realistis sebelum implementasi; gunakan jumlah sel terminal, bukan ukuran piksel web.

- Untuk layout, Unicode, ANSI dan resize, baca [referensi rendering](references/rendering.md).
- Untuk fokus, shortcut, composer dan diagnostik, baca [referensi interaksi](references/interaction.md).
- Untuk verifikasi, jalankan test proyek yang paling sempit dan gunakan `harmonika-terminal-qa` jika tersedia. Self-test fixture hanya bukti harness; penerimaan produk memerlukan aplikasi sebenarnya dalam sesi terisolasi.

Pisahkan event, perubahan state dan rendering. Render dari satu snapshot konsisten; pekerjaan I/O tidak memblokir input. Gunakan identitas request untuk menolak event lama setelah cancel/pergantian request. Saat tidak tersedia akses source binary, hasilkan diagnosis atau patch pada source yang tersedia; jangan patch byte binary.

Nama layanan/model yang ditampilkan adalah **Harmonika Ai**. Backend publik tetap `https://chat.harmonika.id/v1`; key privat tidak tampil di UI, log, snapshot, atau argumen command. Jangan menambahkan konfigurasi akun model eksternal.

Laporkan perilaku yang berubah, bukti tes, viewport yang diuji dan batas yang belum dibuktikan. Jangan menyebut tampilan baik hanya berdasarkan source atau fixture.
