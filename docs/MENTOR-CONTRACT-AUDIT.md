# Audit kontrak mentor

Audit wrapper dan registry pada rilis lokal 2026.10.24.14. Codex mengintegrasikan
perbaikan; worker independen mengaudit parser, interface dan fixture. Source server
Harmonika juga ditinjau read-only; service API chat server tidak diubah.

## Kontrak yang diwajibkan

- Semua factory registry harus menghasilkan AIAdapter; API hanya menerima AIRequest
  terversi, lalu AIEvent atau AIResponse tervalidasi.
- Delta jawaban (`text`) terpisah dari `reasoning` dan `tool_calls`. Fragment tool
  boleh belum lengkap selama stream; complete tool call wajib mempunyai identitas,
  nama dan arguments JSON object yang lengkap. Wrapper tidak mengeksekusi tool.
- Unknown thinking/notice field, conflicting reasoning aliases, invalid usage,
  malformed tool/result, terminal ganda dan event sesudah terminal ditolak.
- Token usage hanya statistik integer nonnegatif/dictionary detail. Error adapter
  mentah atau exception cleanup tidak diteruskan ke TUI; gunakan kode terstruktur.
- Satu blok reasoning awal boleh memakai framing `<think>`. Tag literal di kode
  jawaban setelah blok itu tetap utuh. Structured reasoning lebih diutamakan.
- Batas ukuran/event, sequencing, cancellation, isolasi request dan credential
  tetap berlaku. Tidak ada fallback sesudah text/reasoning/tool delta dikirim.

Field dalam channel yang benar belum menjamin kebenaran semantik isi jawaban.
Tidak ada penghapusan kata 'notice'/'thinking' secara heuristik dari jawaban sah.
Adapter tetap kode tepercaya dan harus memakai error code statis.

## Audit per jalur

| Mentor | Jalur yang ditemukan | Bukti saat ini |
|---|---|---|
| Codex | Melalui engine CLI/team dan teacher agregator | Contract fixture + jalur agregator nyata; pemilihan Codex individual tidak dipaksa |
| OpenCode | Melalui orchestration CLI/team | Contract fixture tool/answer; konektivitas individual belum diuji live |
| Google Mode | runGoogleMode membaca result.answer dan API membungkus chat completion/SSE | Source read-only + fixture reasoning/answer; sesi browser individual belum diuji live |
| WhatsApp AI | metaAutoRouting.attempt menghasilkan answer lalu dibungkus chat completion/SSE | Source read-only + fixture Unicode/answer; akun/bridge individual belum diuji live |

Registry aktual tetap teacher, ollama dan custom. Tidak ada alias palsu yang
mengklaim empat direct adapter sudah tersedia. Setiap direct adapter baru wajib
menggunakan interface dan contract test yang sama sebelum didaftarkan.

## Temuan upstream yang masih perlu dipetakan secara khusus

Source `/opt/harmonika-ai/api/server.js` menunjukkan satu boundary openAIChat.
Google Mode serta Meta memakai reasoning_content untuk teks status/progress;
ini bukan bukti tersedianya token penalaran mentah dari model. Meta menjelaskan
answer dibuffer sebelum dikirim. CLI/team memakai finalAnswerForIntent atas
output proses sebelum dibungkus sebagai completion. Gateway tidak dapat menebak
penalaran asli atau memulihkan token streaming yang tidak diberikan upstream.

Karena itu, hasil audit ini membuktikan constraint seragam di gateway dan parser,
bukan kesuksesan inference langsung ke empat mentor. Pengujian per bridge, mapping
error SDK/proses, dan pembedaan progress vs reasoning asli masih diperlukan pada
adapter khusus. Riwayat/credential tidak diambil untuk pengujian ini.

## Perbaikan dan pengujian

- Menyamarkan raw exception adapter/cleanup; memvalidasi ulang respons custom
  dan menyalin request terpisah untuk primary/fallback.
- Memperketat field, tool schema dan usage; membatasi akumulasi event/tool.
- Memperbaiki tag literal yang tertelan setelah blok reasoning awal.
- 32 tes integration, 23 tes parser dan 14 tes gateway lulus. Empat label mentor
  pada fixture adalah skenario sintetis, bukan klaim empat provider telah diuji.
- Native gateway fixture lulus. Smoke teacher agregator nyata dengan kontrak
  ketat lulus sekitar 4,53 detik. Lihat laporan aktivasi untuk hasil pasca-rilis.

Aktivasi `.14` lulus: SSE sekitar 3,65 detik, native TUI 4,48 detik, auth 401, doctor ok. [Laporan aktivasi](MENTOR-ACTIVATION-REPORT.json).
