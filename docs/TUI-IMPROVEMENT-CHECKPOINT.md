# Checkpoint upgrade terminal — 2026-10-28

Panduan929baris dibaca seluruhnya, termasuk14bagian, mockup dan daftar prioritas.
Sumber: `/Users/id/Downloads/Harmonika_AI_TUI_Improvement_Guide.txt`.

| Pertanyaan | Temuan / keputusan |
|---|---|
| Chat belum ada? | Screenshot menampilkan empty state. PTY membuktikan chat, streaming, Markdown dan highlighted code native sudah ada. |
| Command palette? | Ctrl+P diuji fisik dan sudah ada; tidak diganti. |
| Apakah ASCII/brand diubah? | Tidak. Binary native SHA94f297e1…97956c8 identik. Capture hanya fixture sintetis. |
| LSP UNSTARTED error? | Idle bukan error. auto_lsp sebelumnya false; dengan true dan membaca file TS, beberapa proses LSP mulai otomatis. |
| Config/hooks merah? | Builtin alias berganti menjadi harmonika-config/hooks, tetapi daftar disable masih nama lama; migrasi menambahkan alias yang benar. |
| Apa peningkatan UI? | Workbench status bar, F2 metrics, F3 redacted events, F4 file/code, F5 diff, F7 shell, F8 response progress, F11 zoom. |
| Kode bisa ditindaklanjuti? | Enter syntax-highlighted full code, copy, editor dan Run Python/JS dengan persetujuan manual. |
| Bagaimana angka yang tidak tersedia? | Tampilkan —. Tidak membuat GPU usage, context/token count, biaya atau ETA palsu. |
| Perubahan privilege? | Tidak ada auto-approval global; rename/replace LSP tetap dibatasi guard lama. |
| Bagaimana instalasi? | Incremental signed release dari helper aktif; tidak mengaktifkan source backend lain yang belum dirilis. |
| Framework Rust dipasang? | Tidak diperlukan: native renderer dipertahankan, compositor lokal privat mendukung panel. |
| Source native tersedia? | Yang aktif binary bermerek; binary tidak dipatch, dibongkar atau diganti. |

Codex mengimplementasikan dan menguji. OpenCode melakukan dua review read-only:
audit kondisi awal serta review implementasi. Temuan cache cleanup, metadata
scope, teardown fixture, pesan diff non-repo dan pemulihan mode terminal editor
ditindaklanjuti. Build dependency pertama crash karena stub pipe2 SDK; build
memilih fallback POSIX pipe, lalu PTY nyata lulus. Tidak ada perubahan GPU/DRM.

Fitur IDE penuh seperti hover/autocomplete simbol di editor internal, multi-cursor,
rename lintas file dengan preview per hunk, dan AST editor tree-sitter masih bukan
bagian dari compositor. LSP Q&A, editor lokal dan diff review sudah tersedia;
tidak dilabeli sebagai implementasi ulang IDE. Sound/theme/font baru dari
wishlist bersifat opsional dan tidak mengganti theme/ASCII Harmonika pengguna.

Bukti: `TUI-WORKBENCH-SMOKE.json`, `TUI-WORKBENCH-LSP.json`,
`TUI-WORKBENCH-ACTIVATION.json` dan capture PTY di `docs/mockups/`.
