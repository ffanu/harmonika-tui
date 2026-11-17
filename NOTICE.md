# Atribusi dan batas distribusi

## Komponen TUI yang didistribusikan

Sejak paket **2026.11.17.2**, binary TUI yang dipasang installer adalah **engine Harmonika** —
build ulang dari proyek **opencode** (lisensi MIT, https://github.com/anomalyco/opencode) pada
base upstream `db1d7b9120`, di-rebrand dan dipatch oleh Harmonika ID (`engine/patches/0001..0006`).
Teks lisensi MIT upstream disertakan di [UPSTREAM-LICENSE.md](UPSTREAM-LICENSE.md).

Komponen TUI lama **v0.98.1** (Copyright 2025–2026 Charmbracelet, Inc., FSL-1.1-MIT) tidak lagi
menjadi binary default; teks lisensinya tetap disertakan untuk riwayat dan untuk pengguna yang
masih menjalankannya.

Repository publik ini tidak memuat binary. Binary diterbitkan lewat katalog **bertanda tangan
Ed25519** — `stable.json` (paket), `binaries.json` (binary platform), `engine/update.json`
(engine) — beserta sha256 tiap platform; installer memverifikasi tanda tangan dan checksum
sebelum memasang.

Lapisan launcher, tool dan skill Harmonika dikelola terpisah. Tidak ada pemberian
hak ke akun pemilik, key internal, sesi browser, SSH, database pelanggan, maupun
secret backend melalui source ini. Key API diterbitkan dan dicabut di server;
flag unlimited harus berasal dari server, bukan perubahan konfigurasi client.

Kebijakan lisensi 1 key = 1 perangkat ditegakkan di server: key yang sama akan
ditolak bila dipakai dari perangkat lain (terbukti pada uji lintas perangkat).

