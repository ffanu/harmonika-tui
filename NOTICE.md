# Atribusi dan batas distribusi

Komponen dasar TUI versi 0.98.1: Copyright 2025–2026 Charmbracelet, Inc.
Perubahan merek tidak menghapus lisensi atau atribusi komponen tersebut.
Teks lisensinya disertakan di UPSTREAM-LICENSE.md.

Lapisan launcher, tool dan skill Harmonika dikelola terpisah. Tidak ada pemberian
hak ke akun pemilik, key internal, sesi browser, SSH, database pelanggan, maupun
secret backend melalui source ini. Key API diterbitkan dan dicabut di server;
flag unlimited harus berasal dari server, bukan perubahan konfigurasi client.

Repository publik belum merupakan distribusi binary lintas platform. Artefak
binary kustom, asal build, checksum, target OS dan ketentuan distribusinya harus
ditinjau sebelum rilis publik. Jangan mengganti pemberitahuan upstream dengan
klaim bahwa seluruh source merupakan implementasi asli Harmonika.
