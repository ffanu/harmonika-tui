---
name: harmonika-terminal-qa
description: Uji TUI melalui keyboard dan resize PTY terisolasi, cek lebar sel serta regresi state, dan laporkan bukti fixture terpisah dari aplikasi sebenarnya.
---

Gunakan untuk regresi input terminal, fokus, composer, clipping/ANSI, resize, dan respons terlambat setelah cancel/retry. Baca README/instruksi serta test proyek terlebih dahulu. Pertahankan framework dan dependency yang tersedia.

Jalankan helper tanpa jaringan dan tanpa command arbitrer:

```sh
python3 skills/harmonika-terminal-qa/scripts/terminal_qa.py self-test
```

Helper memakai Python stdlib, reducer referensi dan child fixture miliknya sendiri. Pada POSIX ia mengirim keyboard ke PTY, mengganti ukuran window via ioctl, membaca snapshot JSON, memeriksa draft/fokus/layout, lalu keluar serta memulihkan mode TTY. Output JSON dibatasi, exit nonzero berarti gagal; `pty.status=skipped` berarti bagian PTY belum diuji. Tanpa argumen, helper menjalankan self-test yang sama.

**Ini fixture/reference harness, bukan pengujian binary produksi Harmonika.** Jangan menggunakan hasilnya sebagai bukti bahwa executable atau backend pengguna bekerja. Baca [prosedur QA dan batas bukti](references/qa.md) untuk penerimaan aplikasi sesungguhnya dan detail skenario.

Utamakan keyboard tanpa mouse. Catat expected/actual, viewport, terminal/OS, command, exit code, dan status passed/failed/skipped. Snapshot harus bebas key, cookie, isi akun dan data pribadi. Gunakan fixture dengan data sintetis; jangan attach sesi terminal pengguna atau menghentikan proses yang tidak dibuat pengujian ini.
