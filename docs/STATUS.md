# Status distribusi

Source preview Harmonika TUI, 24 Oktober 2026.

## Sudah diuji

- Instalasi pemilik: rilis 2026.10.24.8, identitas Harmonika, enam skill,
  delapan tool MCP, lima LSP, dokumen Office dan browser Chromium.
- Source: **85 tes lulus**, terdiri dari 55 tes Node.js dan 30 tes Python.
- Browser smoke desktop 1440×900 dan mobile 390×844 dijalankan dengan fixture lokal.
- HOME baru dengan dependency yang diprovisikan eksplisit dapat memulai MCP dan
  membuat proyek. Media ditolak dengan jelas ketika transport publik tidak ada.
- Source checker meninjau kandidat tanpa mencetak secret.

Pengujian HOME baru bukan bukti bahwa installer lengkap telah bekerja di
perangkat baru. Target binary yang tersedia saat ini baru Mac Apple Silicon.

## Belum siap untuk end user publik

1. Binary kustom, asal build, target platform, dan bootstrap fresh install.
2. Transport media HTTPS dengan key dan isolasi pengguna; bridge SSH pemilik
   tidak didistribusikan dan source menonaktifkannya secara default.
3. Entitlement key terminal di backend. Token standar Telegram belum memberi
   coding/tool calling atau unlimited; jangan membagikan key internal pemilik.

API key harus dibuat per pengguna, dikirim melalui saluran privat, dan bisa
dicabut. Unlimited mengikuti hak akses server; client tidak dapat memberikannya.
API tidak memberikan akses SSH, akun browser, CS, ERP, atau database pelanggan.

## Isi repository

Runtime, skill, tes, dokumentasi dan atribusi upstream. Tidak memuat binary
pribadi, API key, signing key privat, SSH key, cookie, database, history sesi,
audit privat, atau direktori dependency pengguna.

Basis TUI tetap menggunakan perangkat lunak upstream dengan lisensi yang
disertakan. Perubahan merek tidak menghapus lisensi dan atribusinya.

Source preview ini memakai identitas dan kontrak artefak `harmonika`, termasuk
nama executable serta environment `HARMONIKA_*`. Source bukan paket update
langsung untuk artefak lama; build native dan installer publik yang cocok masih
harus disiapkan. Perubahan repository tidak mengganti binary pemilik yang aktif.
