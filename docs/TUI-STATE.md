# Sesi dan cache Harmonika TUI

Rilis lokal `2026.10.24.11`, 24 Oktober 2026. Sebelas skill.

## Alur

1. Aplikasi native menyimpan isi percakapan di SQLite direktori sesi.
2. `harmonika state status` membaca metadata dan quick_check secara read-only.
3. `checkpoint` menyimpan ID sesi terbaru per proyek bila diminta.
4. Setelah terminal dibuka kembali, `resume` memvalidasi checkpoint terhadap data
   native dan memberikan argv untuk dilanjutkan oleh pengguna.
5. `clear-cache` menghapus metadata checkpoint; isi chat tetap ada.

```sh
harmonika state status --format text --project /path/proyek
harmonika state list --data-dir /path/data-sesi
harmonika state checkpoint --project /path/proyek --data-dir /path/data-sesi
harmonika state resume --project /path/proyek --data-dir /path/data-sesi
harmonika state clear-cache
```

Bila proyek memakai direktori sesi khusus, sertakan `--data-dir` yang sama pada
checkpoint/resume. Helper memakai `.harmonika` di folder proyek sebagai default.
Native resume biasa tetap tersedia: `harmonika -s ID_SESI` dengan cwd/data-dir yang
benar. Helper tidak otomatis membuka writer atau mengambil alih sesi aktif.

## Data dan keamanan

Cache menyimpan hash path proyek, session_id, updated_at. Maksimum 128 proyek,
TTL 24 jam, transaction SQLite, folder 0700 dan file 0600. Isi chat, judul, key,
cookie dan konfigurasi akun tidak disalin ke cache. Penghapusan cache tidak
menghapus riwayat atau key. Permission ini bukan enkripsi; OS keychain/enkripsi
history belum ditambahkan.

Read native tidak memakai immutable karena bisa melewatkan transaksi WAL.
Database terkunci/tidak dapat dibuka, korup, schema tidak cocok, atau direktori
berisi beberapa database menghasilkan degraded dan tidak menawarkan resume dari
cache lama. Database WAL dingin tanpa sidecar yang tidak dapat dibuka read-only
juga tetap degraded; helper tidak membuka ulang read-write untuk memaksakan
inisialisasi. Tidak ada perbaikan/destructive reset otomatis pada database native.
Resume helper mencakup 100 sesi terbaru; sesi lama tetap dapat dibuka via native.

## Checkpoint QA

- 18 tes state: reopen/resume pointer, WAL aktif, lock, corruption, TTL/eviction,
  isolasi proyek, permission/symlink, concurrency dan clear-cache.
- Pengujian menggunakan fixture SQLite sintetis. Tidak membuktikan percakapan LLM
  lengkap di-resume; tidak mengubah schema atau isi database pengguna.
- CLI terpasang diuji lintas proses: status → checkpoint → resume → clear-cache.
- Rilis ditandatangani dan checksum binary tetap sama; sesi aktif tidak di-attach.

## Backlog berikutnya

- Clipboard dan scrolling mouse: audit dukungan native, adapter OS dan terminal,
  lalu tes bracketed paste/selection/escape sequence sebelum mengubah shortcut.
- Fallback offline: deteksi runtime/model lokal yang benar-benar tersedia,
  entitlement, deadline dan aturan failover sebelum delta pertama. Jangan
  mengganti engine setelah sebagian jawaban tampil tanpa state eksplisit.
- Enkripsi/keychain memerlukan rancangan unlock/recovery tersendiri; key tidak
  disimpan di SQLite cache ini.

## Ringkasan terminal

`harmonika state status --format text` menampilkan ukuran database/WAL/SHM
sebagai batang proporsi file, status cache, jumlah checkpoint berlaku/kedaluwarsa,
dan maksimum lima checkpoint terbaru dengan referensi proyek tersamarkan.
Batang tidak mengukur kapasitas disk atau batas kuota. Checkpoint terbaru bukan
pengukuran proyek teraktif; aktivitas percakapan tidak dicatat oleh cache ini.

Default tetap JSON; `--format json` eksplisit juga tersedia. Status tidak membuat
atau membersihkan cache. Cache rusak/tidak aman menyebabkan status degraded dan
exit code 2. Batas detail backlog ada di [spesifikasi backlog](TUI-BACKLOG.md).
