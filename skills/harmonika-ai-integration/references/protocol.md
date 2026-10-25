# Stream dan context

Parser SSE membaca UTF-8 secara incremental, mendukung LF/CRLF/CR, komentar
heartbeat dan beberapa data line. Blank line mengakhiri event. EOF tidak secara
otomatis mengubah event belum lengkap menjadi final. `[DONE]` adalah kontrak
aplikasi, bukan keyword wajib standar SSE. Gunakan HTTP body streaming untuk
POST; browser EventSource bukan mekanisme yang harus dipasang di TUI.

Reference decoder mengembalikan data event sebagai string. JSON baru di-decode
setelah event lengkap. Collector contoh menerima delta format chat completion
dan legacy started/delta/final/stopped/error; metadata tool bukan teks jawaban.
Native SDK tetap diprioritaskan jika sudah menyediakan parser yang teruji.
Reference ini tidak mengimplementasikan auth, retry, HTTP reconnect, tool runner,
atau jaringan; jangan mengklaim fitur itu dari hasil self-test.

Budget helper memakai upper-bound konservatif ukuran JSON UTF-8 + overhead,
bukan hitungan token model yang tepat. Proyek produksi sebaiknya memasok tokenizer
yang cocok. Helper membuang seluruh giliran user beserta assistant/tool messages,
tetap mempertahankan system prefix dan giliran terbaru. Tool call harus lengkap
dan mempunyai hasil yang berpasangan; jangan memotong pasangan atau menganggap
token/output model tak terbatas hanya karena key request berstatus unlimited.

Referensi primer: [SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation).
