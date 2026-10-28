# Harmonika terminal workbench

Peningkatan lokal berdasarkan seluruh929baris `Harmonika_AI_TUI_Improvement_Guide.txt`.
ASCII, gradien dan brand berasal dari binary native yang sama; hash binary tidak
berubah. Rilis ini menambahkan compositor terminal privat dan memperbaiki konfigurasi
LSP/skill yang tertinggal. Tidak memperbarui paket upstream atau mengubah endpoint.

## Penggunaan

Jalankan `harmonika` seperti biasa pada jendela terminal baru. Sesi yang sudah
berjalan tetap berjalan dengan versi yang dibukanya. `harmonika --no-workbench`
atau `HARMONIKA_WORKBENCH=0 harmonika` membuka TUI native langsung.

| Tombol | Fitur |
|---|---|
| F1 | Panduan shortcut tambahan |
| F2 | CPU/RAM/disk, probe API, sparkline, status PC2 dan metadata token/biaya |
| F3 | Kejadian mekanik teredaksi, filter INFO/WARN/ERROR |
| F4 | File dan pratinjau kode; side pane pada terminal lebar, popup saat sempit |
| F5 | Diff Git tanpa apply/discard otomatis |
| F7 | Shell proyek yang dioperasikan pengguna |
| F8 | Respons aktif dan durasi yang benar-benar tercatat |
| F11 | Zoom/Zen pada pane yang difokuskan |

Ctrl+P, Shift+Tab, input multiline, streaming, Markdown, syntax highlighting,
permission dialog dan chat history tetap menggunakan TUI native. Dalam panel file:
`/` fuzzy search, `Tab` fokus file/kode, Enter membuka syntax-highlighted code penuh,
`c` copy (macOS), `e` editor lokal, `r` menjalankan Python/JavaScript setelah konfirmasi,
dan `q` menutup. F4 menyembunyikan pane dengan editor aktif tanpa membunuh prosesnya.
Jika pane lain masih terbuka ketika chat ditutup, tutup pane itu secara manual
setelah menyimpan pekerjaan. Tidak ada cleanup yang membunuh proses pengguna.

## Batas yang ditampilkan jujur

- Angka API adalah durasi probe metadata, bukan waktu token pertama AI.
- Token/biaya berasal dari metadata SQLite proyek/sesi yang dibaca read-only.
  Nilai yang tidak dilaporkan ditampilkan `—`, bukan estimasi harga atau saldo.
- Status PC2 memakai kontrak diagnostic yang sudah teredaksi; utilisasi GPU dan
  persentase/ETA job tidak dibuat-buat jika engine tidak menyediakannya.
- F3 tidak membuka atau menyalin log backend mentah, prompt, cookie, atau kredensial.
- File explorer menyembunyikan direktori runtime/secret dan menolak symlink keluar
  proyek. Pratinjau dibatasi256KiB. File tidak diedit/dijalankan hanya karena dipilih.
- Inline hover/completion/rename seperti IDE penuh bukan fitur compositor ini.
  Q&A memakai LSP native yang diaktifkan otomatis; tool mutasi LSP tetap mengikuti
  guard dan persetujuan yang berlaku. Tidak ada penambahan auto-approval global.
- Framework Rust, theme/font baru, sound, dan replika editor penuh dari wishlist
  tidak dipasang sebagai dependency baru. Theme Harmonika pengguna dipertahankan.
- Paket compositor yang diuji saat ini khusus macOS Apple Silicon. Platform lain
  tetap masuk native; belum ada klaim hasil uji Linux/Windows.

## Aktivasi dan rollback

Paket incremental ditandatangani Ed25519 dan diperiksa SHA-256. Semua helper lama
diambil dari rilis aktif, sehingga update ini tidak ikut memasang perubahan source
lain yang belum dirilis. Konfigurasi sebelum migrasi disimpan secara privat di
folder backup runtime. `harmonika rollback` mengembalikan pointer rilis; gunakan
backup konfigurasi tersebut jika juga ingin mengembalikan preferensi LSP/diff.

Dependency compositor dibangun privat dari [tmux3.5a](https://github.com/tmux/tmux/releases/tag/3.5a),
[libevent2.1.12](https://github.com/libevent/libevent/releases/tag/release-2.1.12-stable),
dan utf8proc2.10.0; sumber, checksum dan lisensinya disertakan dalam paket.
Build memilih fallback POSIX pipe karena stub pipe2 SDK macOS pada host ini
menyebabkan crash pada build pertama. GPU/DRM tidak diubah.

## Pengujian

`python3 -m unittest discover -s tests -p test_workbench.py -v`

`python3 scripts/smoke-workbench.py --with-lsp`

Smoke memakai PTY, model fixture loopback, config/data/socket dan proyek uji
terpisah. Shortcut dikirim sebagai input terminal sungguhan, lalu isi viewport
dan resize diperiksa. Capture di `docs/mockups/workbench-captures/` adalah hasil
PTY sintetis; gambar PNG adalah render teks capture, bukan rekaman desktop pengguna.

## Perbaikan invalid_tools — rilis 2026.10.28.2

Konfigurasi penuh ternyata mengirim 15 tool native dan 18 tool MCP: total 33,
melewati batas gateway 32. Uji awal workbench hanya memakai fixture MCP minimal
sehingga tidak menemukan regresi ini. Discovery `capabilities` kini menjadi
`media` dengan `action=capabilities`, memakai handler dan validasi yang sama;
pemanggilan legacy masih diterima. LSP, SSH, Telnet, VNC, SEO dan media tetap ada.
Core MCP kini diarahkan ke entrypoint paket bertanda tangan, bukan overlay lama.

`python3 scripts/smoke-tui-tool-budget.py` mengirim sapaan sintetis melalui PTY
dengan seluruh konfigurasi MCP/LSP aktif ke fixture lokal. `--live` memerlukan
kredensial pemilik dan menguji endpoint asli; report hanya mencatat nama/jumlah
tool, status HTTP dan metadata penyelesaian stream. Prompt, jawaban dan key tidak
dituliskan ke report. Buka ulang TUI untuk memuat daftar MCP baru; sesi lama
tidak dihentikan otomatis.
