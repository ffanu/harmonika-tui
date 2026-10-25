# Kontrak sesi dan cache

Riwayat native sudah persisten melalui SQLite. Direktori proyek default adalah
`.harmonika`; `--data-dir` eksplisit mengikuti lokasi data yang benar. Helper
mengenali satu file `.db` dengan signature SQLite dalam direktori tersebut,
memeriksa schema sessions, lalu membaca id, updated_at dan message_count.
Ia tidak membaca titles, messages.parts, prompt atau credential.

Koneksi native memakai mode=ro dan query_only; busy timeout 500 ms dan query
progress deadline 1 detik. Database tidak terbaca/terkunci/ambigu/rusak memberi
status degraded. Tidak ada fallback immutable pada database live. Folder kosong
memberi status empty; bukan bukti hilangnya sesi di lokasi lain.

Cache berada di `~/.config/harmonika/state/session-cache.sqlite3` atau folder
HARMONIKA_CONFIG_DIR/state. Folder 0700, file 0600, tanpa symlink/hardlink;
ini pembatasan akses filesystem, bukan enkripsi. SQLite transaction memastikan
checkpoint utuh; schema version tidak dikenal ditolak tanpa reset otomatis.

Isi cache hanya hash path proyek, ID sesi dan waktu checkpoint. Maksimum 128
proyek; TTL 24 jam dan eviction saat checkpoint berikutnya. Clear-cache hanya
menghapus baris metadata cache. Riwayat native dan API key tidak disentuh.
Checkpoint manual; penyimpanan isi chat otomatis tetap tanggung jawab native.
Resume memvalidasi ID terhadap 100 sesi terbaru di data-dir pilihan, lalu memberi
argv dengan cwd/data-dir/session. Sesi lebih lama dapat dibuka lewat perintah
native biasa setelah ID dikonfirmasi. Helper tidak mengeksekusi argv.

API key tetap di tempat privat milik aktivasi aplikasi, bukan cache/riwayat.
Jangan menyebut permission sebagai enkripsi, dan jangan membuat key baru hanya
untuk perbaikan sesi. Untuk keychain/enkripsi sungguhan diperlukan adapter dan
alur unlock/recovery tersendiri.
