# Prosedur dan cakupan bukti

## Harness yang disertakan

`terminal_qa.py` tidak menerima path executable atau shell command. Ia menjalankan hanya `fixture.py` di direktori helper melalui interpreter Python saat ini dalam isolated mode. Child menggunakan slave PTY; parent tidak mengubah TTY pengguna. Input/output dibatasi, timeout mencegah hang, cleanup hanya menyasar child yang dibuat sendiri. Tidak ada koneksi jaringan, file pengguna, credential, atau dependency pihak ketiga. Import helper tidak menjalankan test.

Reducer menguji: identitas request monoton, cancel menolak delta terlambat, request baru menolak delta/completion lama, completion menolak delta tambahan, fokus menghalangi input ke composer yang tidak aktif, serta error menutup request aktif. Ini model referensi; reducer produksi perlu testnya sendiri.

Layout menguji clipping dalam sel, CJK, combining accent dan subset emoji/ZWJ, baris/kolom viewport kecil, reset SGR serta penghapusan control ANSI non-SGR. Perhitungan Unicode helper adalah subset referensi, **bukan implementasi penuh grapheme/terminal emulator**: flag regional, Indic, ambiguous width, emoji variation tertentu dan pemetaan font perlu renderer proyek serta terminal nyata. Fixture mengirim snapshot JSON, bukan screen buffer VT. Ia tidak membuktikan posisi cursor, scrollback, warna visual, flicker atau rendering glyph terminal.

Tes PTY benar-benar mengirim karakter, Tab, Enter, Escape, refresh, Unicode dan quit ke child raw mode; `TIOCSWINSZ` mengubah ukuran terminal dari 80×24 menjadi 7×2. Refresh eksplisit membaca ukuran baru tanpa mengirim signal ke proses lain. Snapshot membuktikan fokus, draft tetap setelah resize, submit dan cancel. Pengujian ini tidak membuktikan handler SIGWINCH produksi, Shift-Tab, shortcut modifier, Backspace, bracketed paste atau input mouse. Mode TTY dipulihkan oleh fixture pada exit normal/exception; fallback cleanup parent hanya menutup child fixture miliknya. Sistem non-POSIX/tanpa PTY melaporkan skipped.

## Penerimaan aplikasi nyata

Gunakan executable yang telah tersedia dan environment/data-dir/session baru milik tes; jangan mengubah konfigurasi global, mengunduh binary atau menjalankan backend pribadi. Pilih runner PTY/emulator yang telah ada dalam proyek; jangan memaksakan pemasangan framework. Tetapkan timeout, output cap, cleanup terminal dan child, lalu jalankan alur keyboard berikut dengan data sintetis:

| Skenario | Bukti yang dicari |
|---|---|
| Buka layar kosong, Tab/Shift-Tab, bantuan, kembali | Fokus terlihat, urutan konsisten, semua aksi utama tanpa mouse |
| Ketik Unicode, Backspace, paste multiline, draft panjang | Draft/cursor benar, paste tidak submit tak sengaja |
| Submit, stream, cancel, retry dan respons lama | Input responsif; delta lama tidak masuk request baru |
| Resize 80×24 → 20×5 → 120×40 saat mengetik/stream | Draft/fokus bertahan, viewport tidak overflow, scroll valid |
| Output warna, CJK, combining, emoji/ZWJ | Clip aman, tidak pecah glyph, reset ANSI, cursor benar |
| Error backend/tool sintetis lalu retry | Error dapat ditindaklanjuti, draft dan navigasi dapat dipakai |
| Keluar normal dan exception terkontrol | TTY kembali normal, tidak ada child tes tertinggal |

Gunakan server/mock lokal yang sudah ada untuk skenario stream/error. Jangan memakai key nyata di fixture/log. Jika binary atau test backend tidak tersedia, laporkan source checks dan harness sebagai hasil terbatas; tandai penerimaan produksi belum diuji. Tampilan model/layanan memakai Harmonika Ai tanpa konfigurasi akun model eksternal.

Laporan singkat: scope (fixture/production), environment, command + exit code, tabel skenario passed/failed/skipped, expected/actual pada gagal, dan batas bukti. Jangan menyamakan JSON snapshot dengan screenshot atau emulasi terminal lengkap.
