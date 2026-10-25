# Constraint mentor

Registry saat ini: teacher (agregator Harmonika), ollama, custom. Jangan mengklaim
Codex/OpenCode/GoogleMode/WhatsAppAI sebagai empat direct adapter siap sebelum
pemetaan dan tes live masing-masing tersedia. Semua adapter baru harus memakai
AIAdapter dan mengembalikan AIEvent/AIResponse tervalidasi.

Pisahkan text, reasoning dan tool_calls. Unknown thinking/notice fields tidak
boleh diam-diam dianggap content. Mapping SDK/proses adalah tugas adapter, bukan
TUI. Progress browser bukan token penalaran asli; jangan membuat klaim streaming
model dari respons bridge yang dibuffer.

Tool delta adalah fragment; full tool call memerlukan identitas, type=function,
nama dan arguments JSON object utuh. Teks notice di field terlarang ditolak, tetapi
jawaban yang sah tidak dihapus hanya karena menyebut kata 'notice' atau 'thinking'.
Literal tag <think> dalam kode jawaban setelah framing awal harus dipertahankan.
Uji request isolation, conflict reasoning aliases, usage, cleanup exception,
terminal, key scope dan kegagalan sebelum/sesudah output.
