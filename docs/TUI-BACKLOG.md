# Spesifikasi backlog terminal

Status: sebagian sudah diimplementasikan; kontrak transport offline diselaraskan ke SSE kompatibel pada 25 Oktober 2026; lihat [hasil interaksi/gateway](TUI-INTERACTION.md). Spesifikasi berikut tetap menjadi kriteria penerimaan lanjutan. Sesi/cache diprioritaskan pada rilis lokal
2026.10.24.11. Semua identitas layanan yang tampil tetap Harmonika Ai.

## Clipboard dan mouse

- Audit handler native/shortcut yang sudah ada sebelum menambah adapter.
- Aksi Copy menyalin teks jawaban yang dipilih pengguna; kode dan paragraf harus
  utuh, tanpa ANSI. Hindari menyalin tool secrets atau riwayat seluruh sesi.
- Adapter clipboard OS dipanggil melalui argv tetap dan stdin; jangan shell
  interpolation. Jika tidak tersedia, berikan kegagalan jelas. OSC52 harus opt-in
  sesuai kemampuan terminal/SSH, dengan batas ukuran dan tanpa mencetak payload
  ke log. Jangan aktifkan OSC52 diam-diam.
- Wheel/trackpad hanya menggulir viewport yang tepat. Native text selection harus
  tetap tersedia lewat modifier; mode mouse tidak mengambil alih paste keyboard.
- Pulihkan mode mouse, cursor dan alternate screen pada exit/error. Pertahankan
  keyboard sebagai jalur penuh untuk semua aksi.
- Uji PTY/emulator: copy Unicode/kode panjang, clipboard kosong/gagal, terminal
  remote tanpa clipboard, wheel saat stream, selection, resize, bracketed paste,
  exit/exception dan terminal yang tidak mendukung mouse.
- Syarat selesai: tes adapter + pengujian visual terminal nyata; fixture JSON
  saja tidak membuktikan clipboard sistem atau selection.

## Fallback offline lokal

- Default tetap cloud Harmonika. Adapter lokal harus opt-in; verifikasi daemon dan
  model yang benar-benar terpasang sebelum menyatakan offline tersedia.
- Akses awal hanya loopback lokal, tanpa redirect ke host luar. Discovery dan
  stream SSE kompatibel memakai deadline terpisah, cancellation serta generation ID yang sama
  dengan controller. Tidak mengirim key cloud ke daemon lokal.
- Failover otomatis hanya sebelum delta/tool call pertama dan hanya untuk
  kegagalan koneksi/availability; jangan mengakali 401/403/kuota entitlement.
- Jika stream terputus setelah teks tampil, tandai interrupted dan minta retry;
  jangan menyambung jawaban dari model berbeda seolah satu respons.
- Petakan kemampuan context/tool/media dari model aktual. Tool execution tetap
  melalui guard dan approval; teks model tidak boleh menjadi shell langsung.
- Tidak mengunduh model/menyalakan layanan otomatis. Batasi context, antrean,
  token/output dan concurrency untuk melindungi RAM/VRAM.
- Parser lokal memakai SSE OpenAI-compatible `/v1/chat/completions`, termasuk delta,
  finish_reason, terminal `[DONE]`, potongan UTF-8/frame, error dan EOF prematur.
  Jangan memasukkan NDJSON `/api/chat` ke decoder SSE.
- Uji daemon mati, model hilang, respons parsial, JSON rusak, timeout, Stop,
  tool call, koneksi pulih dan kesesuaian riwayat. Uji mock wajib diikuti smoke
  daemon nyata sebelum flag fitur aktif.

## Keputusan berikutnya

Lanjutkan uji terminal fisik serta smoke daemon/model lokal sebelum aktivasi
fallback nyata. Backend registry dan helper CLI memakai guard hot-reload;
binary native tetap dipertahankan. Tidak ada instalasi model otomatis.

## Referensi adapter

- [XTerm control sequences](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html):
  audit mode mouse, bracketed paste dan clipboard terminal terhadap emulator target.
- [API lokal](https://docs.ollama.com/api/tags) untuk daftar model dan
  [API chat kompatibel](https://docs.ollama.com/api/openai-compatibility): adapter memakai
  GET /api/tags serta POST /api/show untuk readiness, lalu POST
  /v1/chat/completions dengan stream=true pada loopback yang dikonfigurasi.
  Transportnya SSE, sesuai adapter aktual; `/api/chat` NDJSON tidak digunakan.
  Perubahan dokumentasi ini tidak menyatakan daemon/model lokal sudah ready.
