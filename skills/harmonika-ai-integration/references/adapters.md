# Kontrak adapter inference v1

Runtime `tools/ai_integration.py` menyediakan AIRequest(messages, tools,
generation, context), AIAdapter.stream/complete, AIEvent dan AIResponse.
AIIntegration mengatur sequencing, cancellation, validasi hasil dan failover.
`tools/ai_adapters.py` menyediakan registry/factory teacher, ollama dan custom.
Gateway menerjemahkan wire API native tanpa perubahan binary TUI.

Model, endpoint dan key ditentukan adapter, bukan generation/context pelanggan.
Factory extension menerima key kosong secara default; resolver credential per
adapter harus eksplisit. Jangan menyimpan key/prompt dalam log atau repr.

Event internal: delta(text/reasoning/tool_calls), usage(tokens), completed.
Stream harus mempunyai tepat satu terminal terakhir. Wrapper memvalidasi iterator
sudah selesai sebelum meneruskan terminal, dan close consumer harus menutup sumber.
Tindakan tool tetap tanggung jawab executor dengan guard, bukan adapter inference.

Gunakan payload yang sama untuk contract test teacher/local/custom; adapter tanpa
HTTP harus dapat dihubungkan tanpa mengubah TUI. Uji key isolation, parameter,
riwayat role/tool, reasoning, sequence, cancel, error dan larangan failover setelah
output. Runtime/model nyata yang belum ada tetap dilaporkan tidak siap.
