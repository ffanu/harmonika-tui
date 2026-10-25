# Interaksi dan streaming Harmonika TUI

Helper lokal bertanda tangan `2026.10.25.31`. Binary TUI tetap sama.

## Mouse dan clipboard

Mouse/wheel native sudah aktif dengan `options.tui.mouse=true` dan
`options.tui.scrollbar=always`. Scroll roda/trackpad bekerja ke dua arah,
termasuk satu tick pelan dan burst cepat. Blok teks/kode dalam jawaban dengan
mouse untuk menyalinnya langsung ke clipboard OS; tidak perlu parser mouse
kedua yang berebut input dengan binary native.

Smoke executable asli memverifikasi respons kode170baris, SGR1002/1006,
scroll satu baris/tick, resize100×32 ↔80×24, seleksi kode yang cocok tepat dengan
substring sumber, clipboard macOS nyata dan keluar bersih. Format clipboard
snapshot QA disimpan di RAM dan dipulihkan; perubahan pengguna yang dikenali
bukan fixture dipertahankan. Pemeriksaan dilakukan lewat PTY dengan event
sintetis; bukan klaim pengujian hardware mouse manual atau desktop Linux.

[Report native](NATIVE-INTERACTION-REPORT.json). Dugaan awal wheel-down macet
terbukti kesalahan emulator QA yang belum mengimplementasikan scroll region
XTerm. Emulator diperbaiki dan dibandingkan dengan pyte; binary tidak dipatch.

```sh
harmonika clipboard status
printf 'Teks untuk disalin' | harmonika clipboard copy
```

Adapter clipboard memakai executable OS dengan stdin, batas 1 MiB, timeout
3 detik, serta pembersihan ANSI/control. Tidak membaca clipboard secara implisit,
tidak mencetak payload, dan tidak mengaktifkan OSC52. Adapter macOS tersedia;
adapter Linux belum diuji pada desktop Linux. Perintah ini adapter CLI tambahan. Copy seleksi mouse menggunakan implementasi
native yang sudah tersedia, bukan shortcut baru pada binary.

## Gateway aplikasi

```sh
harmonika network status
harmonika network serve
```

Pada instalasi pemilik, LaunchAgent `id.harmonika.tui.gateway` menjalankan
serve di `127.0.0.1:18184`. Config provider native diarahkan ke URL lokal tersebut
setelah smoke cloud nyata lulus. Server meminta Bearer key privat yang sama;
key tidak ditaruh pada argv/plist/log. Hanya identitas Harmonika Ai yang dikirim
pada metadata model. Ini loopback pribadi, bukan endpoint API publik baru.
`serve` manual hanya diperlukan ketika tidak memakai service; jangan jalankan
instance kedua pada port yang sama.

Service memakai launcher yang memverifikasi signature/checksum. Rilis baru dan
rotasi key memerlukan restart service yang terencana agar proses mengambil file
baru. Rilis ini tidak merestart/attach writer sesi pengguna. Config sebelumnya
tersimpan privat untuk pemulihan. Public installer tetap memiliki blocker lama.

## Streaming reasoning

Gateway memisahkan structured reasoning_content dan framing `<think>` di awal
respons menjadi delta reasoning tersendiri, bukan teks jawaban. Tag bisa terbelah
antar chunk. Teks kode yang memuat tag setelah jawaban dimulai tetap literal.
Reasoning yang tidak ditutup tidak diberi status sukses. Tool call dipertahankan.
Normalizer memakai helper parser yang sama dengan tes; startup/native gateway
fixture membuktikan jawaban tampil tanpa reasoning masuk ke teks jawaban.

## Fallback offline

Engine failover aktif, tetapi status lokal baru ready bila daemon loopback dan
model lokal nyata ditemukan. Saat pemasangan ini, `local.ready=false` karena
runtime tidak merespons; tidak ada model yang diunduh atau instalasi dilakukan.

- Cloud tetap utama. Failover hanya untuk gangguan koneksi/timeout atau
  HTTP 500/502/503/504 sebelum frame bermakna/terminal diteruskan.
- 400/401/403/429 tidak dialihkan. Stream yang sudah menghasilkan output tidak
  disambung dari model lain; ia ditandai error/interrupted.
- Discovery melalui `/api/tags` dan `/api/show`; model remote/cloud ditolak.
  Jika hanya satu model lokal terpasang ia dapat dipilih otomatis. Beberapa model
  memerlukan local_model pada file privat `tui-network.json` dan restart service.
- Transport lokal memakai endpoint kompatibel `/v1/chat/completions`, bukan
  memasukkan NDJSON ke decoder SSE. Key cloud tidak diteruskan ke model lokal.
- Model tanpa kemampuan tools ditolak bila request membawa tools. Input media
  belum didukung oleh jalur fallback ini; tidak ada downgrade diam-diam.
- Maksimum dua request aktif, body 2 MiB, frame 128 KiB, stream 8 MiB dan total
  deadline 120 detik; timeout socket 20 detik. Stop/disconnect menutup generator
  pada write berikutnya; sebelum output pertama deteksi disconnect belum instan
  dan dapat menunggu timeout socket. Tidak ada klaim pembatalan remote job instan.

## Bukti

- Parser: 23 tes, termasuk reasoning/UTF-8/tag terbelah dan EOF/terminal.
- Gateway: 14 tes failover, auth HTTP loopback, tool call, respons parsial,
  reasoning terstruktur, serta pencegahan jawaban ganda setelah terminal.
- Clipboard: 3 tes adapter, batas input dan kegagalan. Roundtrip OS macOS juga
  lulus; seluruh format clipboard sebelumnya disimpan di RAM dan dikembalikan.
- Native gateway fixture: executable asli exit 0, jawaban terlihat dan reasoning
  tidak masuk teks jawaban. Tidak memakai sesi pengguna.
- Cloud nyata melalui service lokal: streaming selesai sekitar 5 detik.

Referensi protokol: [kompatibilitas API lokal](https://docs.ollama.com/api/openai-compatibility)
dan [XTerm](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html).

## Pipeline gambar

Tool MCP `generate_image` memakai bridge async RTX terlebih dahulu pada instalasi
pemilik. [Kontrak, batas dan tes nyata](RTX-TUI-BRIDGE.md). Vision RTX belum siap;
`analyze_image` memakai engine Harmonika sebelumnya sebelum dispatch GPU.
Helper baru dimuat pada proses MCP baru; sesi pengguna yang sedang berjalan
tidak ditutup otomatis. Pembaruan otomatis tetap nonaktif.

## Ulangi QA native

Jalankan satu pengujian clipboard pada satu waktu di macOS yang mempunyai sesi
GUI. Tidak memakai akun/model asli; konfigurasi, socket dan history terisolasi.

```sh
python3 scripts/smoke-native-interaction.py
python3 -m venv /tmp/harmonika-native-qa
/tmp/harmonika-native-qa/bin/pip install pyte==0.8.2
/tmp/harmonika-native-qa/bin/python scripts/validate-native-wheel.py
```

pyte hanya dependency QA; tidak diperlukan oleh runtime/backend produksi.
