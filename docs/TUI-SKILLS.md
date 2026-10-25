# Modul pengembangan Harmonika TUI

Checkpoint 24 Oktober 2026. Rilis instalasi lokal: `2026.10.24.9`.

| Modul | Implementasi | Bukti |
|---|---|---|
| harmonika-tui-dev | Instruksi layout berbasis sel, resize, keyboard, composer dan ANSI | Validator dan discovery native |
| harmonika-async-concurrency | Controller async referensi dengan antrean terbatas, generation ID, timeout dan Stop | 5 tes serta self-test |
| harmonika-ai-integration | Parser SSE/UTF-8 incremental dan trimming riwayat dengan pasangan tool utuh | 11 tes serta self-test |
| harmonika-terminal-qa | Reducer/layout referensi dan fixture PTY milik harness | 6 tes serta self-test keyboard/resize |

## Pemakaian

```sh
harmonika doctor
harmonika skill terminal-qa self-test
harmonika skill async-concurrency self-test
harmonika skill ai-integration self-test
```

Minta Harmonika menggunakan skill yang sesuai ketika mengerjakan proyek TUI.
Enam skill sebelumnya tetap tersedia. Baca helper dan referensi sebagai file
publik read-only; penulisan ke instalasi dan akses credential tetap diblokir.

## Q&A penerimaan

- Apakah event loop binary diubah? Tidak. Binary tetap identik; modul ini adalah
  skill dan implementasi referensi. Latensi API dan rendering aplikasi yang sedang
  streaming perlu pengujian produk tersendiri.
- Apakah startup nyata diuji? Ya, pada PTY dengan socket, proyek dan data-dir
  terpisah. Keempat skill tampil; tidak mengirim prompt dan tidak attach sesi pengguna.
- Apa yang diuji? 110 tes otomatis, validator empat skill, tiga self-test dan
  Chromium fixture desktop/mobile. Controller menolak event request lama; parser
  menahan frame/UTF-8 terpotong dan tidak menggandakan teks final.
- Apa batasnya? Perhitungan lebar Unicode harness hanya subset; bukan emulator
  terminal lengkap. Async cancellation bersifat kooperatif. Helper protokol tidak
  mengimplementasikan HTTP/auth/tool execution atau reconnect jaringan.
- Apakah distribusi publik selesai? Belum. Lihat [status distribusi](STATUS.md).

Codex mengintegrasikan dan menguji; worker terminal menyiapkan skill layout/QA;
OpenCode mengaudit whitelist, dispatcher, packaging dan pemisahan klaim fixture/native.
Rilis sebelumnya tetap tersedia untuk rollback; tidak ada auto-update.
