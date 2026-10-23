# Harmonika TUI

Terminal AI kustom dengan enam skill Harmonika, tool MCP, LSP, dan pembaruan
manual bertanda tangan. **Status: source preview; installer publik belum siap.**

Paket kustom Harmonika untuk TUI dasar 0.98.1: binary bermerek milik pemilik,
skill proyek, tool MCP, LSP, dan pembaruan manual dengan verifikasi serta rollback.

Tidak mengunduh update saat startup. Binary `crush.orig` tetap disimpan oleh pemilik.
Skill impor Claude yang belum kompatibel dinonaktifkan pada konfigurasi Crush;
file asli tidak dihapus. Gunakan enam skill Harmonika yang diuji di paket ini.
API model memakai `https://chat.harmonika.id/v1`; kredensial tetap di konfigurasi
privat pengguna dan tidak masuk paket rilis. Basis TUI tetap Crush; atribusi dan
lisensi upstream harus dipertahankan, bukan diganti dengan klaim karya asli.

## Alur kerja

1. Jalankan `harmonika doctor` sebelum mengubah instalasi.
2. Buka `harmonika` atau `crush` di folder proyek. Dari Home, gunakan tool
   `create_directory` untuk membuat proyek Downloads/Projects.
3. Skill memberikan alur UI, debug, pengujian browser, dokumen, Git, dan rilis.
4. `harmonika update --check` hanya memeriksa katalog rilis Harmonika.
5. Pemasangan rilis dilakukan manual dari paket bertanda tangan; versi sebelumnya
   tetap tersedia untuk `harmonika rollback`. Sesi aktif tidak dihentikan.

## Status sebelum publikasi GitHub

Instalasi pemilik sudah berjalan pada rilis **2026.10.24.8**. Repository ini
**belum merupakan installer publik siap pakai**. Pemeriksaan publik memiliki
perintah terpisah:

```sh
python3 runtime/check-public-source.py
python3 runtime/public-preflight.py
python3 runtime/license.py check
```

Source checker menghindari mencetak secret dan meninjau Git index bila repository
sudah dibuat. Tanpa Git, ia memeriksa allowlist source. Preflight publik sengaja
gagal sampai blocker distribusi diselesaikan. Audit/log, signing key privat,
key API, SSH key, database, backup binary dan history tidak boleh masuk GitHub.

Persiapan saat ini: SDK/Node/tool paths memakai HOME atau environment yang
eksplisit, tidak lagi username pemilik; SSH pemilik menjadi opt-in tanpa target
default. Perubahan source ini belum dipasang di rilis pemilik. Key individual
dapat diperiksa ke endpoint HTTPS Harmonika melalui `license.py`; aktivasi
memakai prompt rahasia, mode file 0600, dan tidak mengganti key lama bila gagal.

Yang masih memblokir publik: artefak binary kustom untuk fresh install, transport
media HTTPS yang terisolasi per key, dan entitlement key terminal pada server.
Token standar bot Telegram saat ini belum memberi coding/tool calling atau
unlimited. Jangan membagikan token internal pemilik untuk mengatasi hal tersebut.

Alur rilis yang dituju: admin membuat satu key untuk satu pengguna lewat Telegram,
memilih entitlement unlimited bila diizinkan, memberikan key secara privat,
pengguna mengaktifkan di terminal, dan backend memvalidasi/revoke key pada setiap
request. Unlimited merujuk kuota request key; concurrency dan kapasitas worker
tetap berlaku. Source client tidak bisa menaikkan hak akses server.

Helper aktivasi yang disiapkan: `python3 runtime/license.py activate`. Key diminta
melalui prompt tersembunyi, bukan argumen command line. Hak coding harus sudah
diberikan backend; helper akan menolak key standar yang belum mendapat hak itu.

Target yang telah diuji baru Mac Apple Silicon; dukungan Windows/Linux belum
diverifikasi. Lihat NOTICE.md dan UPSTREAM-LICENSE.md sebelum distribusi.

## Pengembangan

- `runtime/`: launcher, installer/paket rilis, pemeriksaan kesehatan.
- `skills/`: paket SKILL.md dan skrip pendukung.
- `tests/`: pengujian terisolasi; jangan gunakan sesi atau dokumen pelanggan.
- `docs/`: status kesiapan dan batas distribusi publik. Audit privat tidak diunggah.

Verifikasi lokal:

```sh
node --test tests/mcp-core.test.cjs tests/project-access.test.cjs tests/project-file-guard.test.cjs tests/skills_browser_test.cjs
$HOME/.config/harmonika-terminal/document-runtime/bin/python -m unittest discover -s tests -p '*test*.py' -v
node tests/skills_browser_integration.cjs
```

Rilis pemilik: `python3 runtime/package-release.py YYYY.MM.DD.N`, lalu
`harmonika update --apply dist/harmonika-terminal-YYYY.MM.DD.N.zip`.
Penandatanganan hanya memakai key lokal pemilik; kunci privat tidak ikut paket.
Untuk bootstrap pertama pada instalasi Mac ARM64 ini, gunakan `runtime/install.py`
dengan paket yang sudah disiapkan. Jangan jalankan bootstrap ulang pada instalasi
aktif. Status kesiapan dan batas distribusi ada di [docs/STATUS.md](docs/STATUS.md).

## Perintah skill

```sh
harmonika skill documents capabilities
harmonika skill browser --url http://localhost:3000 --out /path/proyek/qa
```

Helper dijalankan melalui launcher yang diverifikasi. Model tidak perlu menjalankan
path privat `.config` melalui bash. Chromium memakai profil uji terisolasi; tidak
meminjam cookie browser pelanggan. Pembuatan Office memakai pustaka lokal dan tidak
mengeksekusi formula Excel. Browser QA adalah smoke test, bukan jaminan aksesibilitas
atau keamanan penuh. DOCX/XLSX/PPTX menggunakan versi dependensi yang dipasang manual.

Dokumentasi/katalog rilis: `https://chat.harmonika.id/assets/harmonika-terminal/`.
Paket publik hanya runtime/skill, tidak berisi binary, token, cookie atau riwayat.

Jangan commit token, kunci SSH, signing key privat, konfigurasi akun, atau history.
Jangan upgrade paket npm Crush karena itu melewati paket kustom yang dikunci.
Runtime Python/Node, LSP, Whisper, Playwright, dan pustaka dokumen adalah dependensi
yang harus diuji serta dicatat; paket ini tidak menjanjikan semua fitur tersedia
ketika dependensinya belum dipasang. Tidak ada update otomatis dependensi.
