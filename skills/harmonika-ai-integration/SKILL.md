---
name: harmonika-ai-integration
description: Menghubungkan chat TUI ke inference streaming, memvalidasi SSE/UTF-8, membatasi context, dan memetakan lifecycle respons tanpa duplikasi atau kebocoran key.
---

Gunakan ketika mengubah adapter AI, parsing stream, role/history, batas context,
atau state respons terminal. Default layanan aplikasi adalah Harmonika Ai melalui
`https://chat.harmonika.id/v1`, model `harmonika-ai`. Jangan mengganti provider,
menambah akun pihak ketiga, atau membuka endpoint lokal hanya karena contoh
arsitektur menyebut model lain. Adapter lain harus dipilih eksplisit oleh proyek.
Identitas layanan yang tampil tetap Harmonika Ai; key tidak masuk argv atau log.

Periksa kontrak dan capability key yang sebenarnya sebelum mengirim tools/media.
Tool decision bukan tool execution: validasi nama/argumen lengkap dan persetujuan
pengguna sebelum menjalankan tindakan. Jangan menjalankan potongan tool JSON yang
masih di-stream, menampilkan argumen sebagai jawaban, atau melewati scope file.

Gunakan parser incremental: batas TCP bukan batas SSE, token, UTF-8 atau JSON.
Pisahkan status waiting, delta, completed, stopped dan error. Final tidak boleh
menggandakan teks yang sudah dikirim sebagai delta; stream putus tanpa terminal
event harus ditandai interrupted, bukan selesai. Sanitasi kontrol terminal dari
teks tak tepercaya sebelum merender; pertahankan konten kode/dokumen aslinya.

Untuk history, simpan role dan hubungan tool-call/result. Pertahankan system
prompt dan giliran terbaru, buang giliran lama utuh sesuai budget/tokenizer,
bukan karakter acak atau setengah tool pair. Bila prompt utama sendiri terlalu
besar, berikan error/compact yang jelas. Jangan memindahkan history antar pengguna.

Baca [kontrak stream/context](references/protocol.md) saat mengubah parser atau
context cleaner. `scripts/stream_protocol.py` adalah reference parser dan helper
budget, bukan pengganti SDK transport produksi. Jalankan `harmonika skill
ai-integration self-test`; kemudian uji adapter proyek dengan stream sintetis
dan endpoint yang memang diizinkan. Jangan mengklaim latency/streaming provider
nyata dari pengujian fixture.

Untuk adapter modular, gunakan kontrak runtime `AIRequest`, `AIEvent`, `AIResponse`
dan `AIAdapter`; baca [kontrak adapter](references/adapters.md). Kode TUI tidak
memilih endpoint/model atau memegang credential per adapter. Pertahankan wire
API gateway yang ada saat mengganti engine.

Saat mengaudit mentor, baca [constraint mentor](references/mentors.md). Bedakan
fixture kontrak, source review agregator, dan tes konektivitas mentor individual.
