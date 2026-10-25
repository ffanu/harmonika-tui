# Kesiapan skill Harmonika AI dan TUI

Audit instalasi pemilik rilis `2026.10.24.14`, 24 Oktober 2026. Tabel ini menilai
fitur melalui integrasi backend/MCP dan helper TUI yang terdaftar. Siap berarti
fungsi tersebut berhasil dijalankan pada cakupan yang disebutkan; keberadaan
library atau kemampuan model menulis kode tidak dianggap skill lengkap.

| Fitur yang diminta | Backend | Harmonika TUI | Batas utama |
|---|---|---|---|
| docx | Belum API pembuat khusus | Terbatas, create/read berhasil | Paragraf dasar; edit/layout proposal profesional belum helper standar |
| pdf | RPC lama tetap ekstraksi teks | Lokal read/create dan edit halaman siap | 10 MiB, ekstraksi 20 halaman/30.000 karakter; OCR dan edit teks bebas belum tersedia |
| xlsx | Belum API pembuat khusus | Terbatas, create/read berhasil | Tabel dasar; grafik dan kalkulasi formula belum tersedia pada helper |
| pptx | Belum API pembuat khusus | Terbatas, create/read berhasil | Judul/bullet; desain slide, animasi dan grafik belum helper standar |
| charts | Belum modul renderer khusus | Belum skill charts | Kode Mermaid/SVG/HTML bisa ditulis AI; render/export standar belum teruji |
| LLM | Siap melalui agregator | Siap, gateway kontrak aktif | Empat mentor individual belum seluruhnya diuji live; offline lokal belum siap |
| VLM | Siap melalui mentor | Siap melalui analyze_image | Uji OCR sintetis benar; maksimum 10 MiB. Vision Windows RTX belum aktif |
| ASR | Endpoint backend belum transkripsi nyata | Siap lokal CPU | Model base tersedia; audio maksimal 120 detik pertama |
| TTS | Belum API Harmonika khusus | Terbatas pada suara OS macOS | say berhasil membuat fixture audio; belum skill/tool TTS standar lintas platform |
| image-generation | Siap, termasuk generate RTX nyata | Siap generate_image | Uji menghasilkan PNG 512×512; preview terminal bergantung emulator |
| image-edit | Jalur edit khusus belum terverifikasi | Belum tool image-edit | Tidak ada ref input pada MCP generate_image; worker RTX references=false |
| image-search | Belum API hasil gambar terstruktur | Belum tool image-search | Web search umum bukan image search dengan thumbnail/metadata gambar |
| video-understand | Siap analisis frame | Terbatas melalui analyze_video | Enam frame, durasi input maksimal 10 menit; audio video tidak dianalisis |
| fullstack-dev | Engine coding tersedia | Fondasi tersedia, belum skill khusus | File/bash/LSP ada; workflow Next.js lengkap hingga deploy belum diaudit end-to-end |
| agent-browser | Bridge tertentu tersedia | Browser QA siap, agent tindakan belum lengkap | Navigasi/screenshot Playwright diuji; tool klik/fill/form generik belum terdaftar |
| web-search | Siap | Siap | Uji mengembalikan jawaban dan 5 sumber; kebenaran setiap fakta tetap perlu sumber terverifikasi |

Status backend dokumen membahas API **pembuatan** khusus, bukan penolakan bahwa
parser lampiran atau model umum dapat membantu membaca/menulis konten. Audit ini
tidak membuka semua aplikasi/domain pada VPS atau menguji setiap endpoint member
secara live. Hasil image-edit di luar jalur MCP/RTX ditandai belum terverifikasi,
bukan dianggap sudah mendukung edit karena engine bisa menerima gambar.

## Skill yang benar-benar terpasang

1. harmonika-ui
2. harmonika-debug
3. harmonika-browser-qa
4. harmonika-documents
5. harmonika-git
6. harmonika-release
7. harmonika-tui-dev
8. harmonika-async-concurrency
9. harmonika-ai-integration
10. harmonika-terminal-qa
11. harmonika-state-persistence

Nama docx/pdf/xlsx/pptx saat ini dicakup skill harmonika-documents, belum merupakan
empat skill mandiri. Backend juga tidak menjalankan 11 skill TUI sebagai endpoint
HTTP terpisah; ia menyediakan operasi inference/media yang dipanggil tool.

## Tool MCP aktif

create_directory, capabilities, web_search, analyze_image, read_pdf,
analyze_video, transcribe_audio, generate_image.

## Bukti uji aktual

- DOCX/XLSX/PPTX dibuat lalu di-inspect kembali: semua exit 0.
- Gambar sintetis: OCR membaca HARMONIKA 4821 dengan benar (~5,1 detik).
- PDF sintetis satu halaman: teks PDF AUDIT 5932 terbaca (~0,56 detik).
- Suara sintetis: ASR membaca nomor 7492 (~1,65 detik).
- Video sintetis: enam frame mengenali teks 4821 (~6,75 detik), tanpa analisis audio.
- Web search: berhasil dengan lima sources (~8,6 detik). Isi harga/cakupan dari
  jawaban tidak disalin ke knowledge base atau digunakan sebagai data terverifikasi.
- Generate lewat tool TUI/MCP: PNG 512×512, 476.853 byte (~1,65 detik).
- Browser smoke Chromium: desktop 1440×900 dan mobile 390×844 lulus.
- Doctor: signature/runtime/MCP sehat, 11 skill dan lima LSP terdaftar; ini bukan
  bukti setiap language server atau empat mentor individual sudah diuji aktif.

Semua input uji adalah sintetis dan berada pada direktori sementara terisolasi.
Tidak ada model/dependency baru yang dipasang untuk audit ini; tidak ada perubahan
service, token, riwayat pelanggan atau routing. [Laporan ringkas JSON](SKILL-READINESS-REPORT.json).

## Yang belum siap penuh dari daftar kebutuhan

Layout PDF lanjutan, charts/diagram renderer, Office styling/editing,
Excel charts/formula calculation, presentasi profesional, TTS API, image edit,
image search, fullstack-dev terarah dan agent-browser klik/form lintas situs.
Vision RTX juga belum siap meskipun VLM melalui mentor sudah berfungsi.

Public installer dan entitlement key pengguna umum tetap memiliki blocker pada
[status distribusi](STATUS.md); hasil di atas khusus instalasi pemilik yang diuji.

## Pembaruan dispatcher dokumen

Refactoring lokal menambahkan PDF berbasis pypdf/ReportLab dan handler Office terpisah.
Status dan bukti pengujian terbaru: [kontrak dokumen lokal](DOCUMENTS.md).
Audit media/MCP di atas tetap merupakan hasil rilis .14; pembaruan dokumen tidak
mengubah kemampuan vision, audio, atau endpoint backend HTTP.
