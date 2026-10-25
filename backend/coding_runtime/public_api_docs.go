package main

import (
	"fmt"
	"net/http"
	"strings"
)

const publicAPIDocumentationVersion = "2026-10-24-coding-phase2"

type publicAPIEndpoint struct {
	Method      string `json:"method"`
	Path        string `json:"path"`
	Description string `json:"description"`
	Response    string `json:"response"`
}

func publicAPIEndpoints() []publicAPIEndpoint {
	return []publicAPIEndpoint{
		{"GET", "/v1/coding/debug/sessions/{id}", "Status sesi debug milik token/workspace", "application/json"},
		{"GET", "/v1/coding/debug/sessions/{id}/events", "Replay event debugger terisolasi", "application/json | text/event-stream"},
		{"POST", "/v1/coding/debug/sessions/{id}/actions", "Continue/next/inspect dengan Idempotency-Key", "application/json"},
		{"POST", "/v1/coding/debug/sessions/{id}/stop", "Tutup sesi debug sendiri dan simpan snapshot tervalidasi", "application/json"},
		{"GET", "/v1/coding/capabilities", "Kesiapan skill coding workspace terisolasi", "application/json"},
		{"POST", "/v1/coding/workspaces", "Workspace privat per token dengan izin eksplisit", "application/json"},
		{"GET", "/v1/coding/workspaces", "Daftar workspace token sendiri", "application/json"},
		{"GET", "/v1/coding/workspaces/{id}", "Metadata dan versi workspace sendiri", "application/json"},
		{"POST", "/v1/coding/workspaces/{id}/agent", "Pemilihan skill otomatis dalam workspace privat", "application/json | text/event-stream"},
		{"POST", "/v1/coding/workspaces/{id}/invoke", "Jalankan skill workspace; exec dan commit menunggu persetujuan", "application/json"},
		{"GET", "/v1/coding/jobs/{id}", "Recovery status job", "application/json"},
		{"GET", "/v1/coding/jobs/{id}/events", "Replay event stdout/stderr dengan Last-Event-ID", "application/json | text/event-stream"},
		{"POST", "/v1/coding/jobs/{id}/approve", "Persetujuan eksplisit terikat input_hash dan versi workspace", "application/json"},
		{"POST", "/v1/coding/jobs/{id}/stop", "Hentikan job milik token sendiri", "application/json"},
		{"GET", "/v1/public-ai/capabilities", "Fitur dan batas token publik", "application/json"},
		{"GET", "/v1/public-ai/endpoints", "Katalog endpoint publik dan versi dokumentasi", "application/json"},
		{"GET", "/v1/public-ai/guide.txt", "Unduh panduan terbaru; tidak menampilkan kembali token lama", "text/plain"},
		{"GET", "/v1/models", "Daftar model kompatibilitas; gunakan alias default untuk integrasi", "application/json"},
		{"POST", "/v1/chat/completions", "Chat teks: JSON biasa atau SSE bila stream=true", "application/json | text/event-stream"},
		{"POST", "/v1/public-ai/chat/messages", "Chat teks native; selalu SSE", "text/event-stream"},
	}
}

func publicAIFeatures() map[string]any {
	return map[string]any{
		"chat": true, "streaming": true, "openai_compatible": true,
		"coding_phase2": publicCodingPhase2Ready(), "coding_debugger": publicCodingPhase2Ready(), "coding_refactor": publicCodingPhase2Ready(),
		"coding_workspaces": publicCodingReady(), "coding_agent": publicCodingReady(), "coding_sandbox": publicCodingReady(),
		"orchestration": true, "model_fallback": true, "web_grounding": true,
		"endpoint_catalog": true, "guide_download": true, "tool_calls": false, "tool_execution": false,
		"files": false, "file_upload": false, "image_input": false, "pdf_input": false,
		"image_generation": false, "image_generation_sse": false,
		"image_preview": false, "image_download": false,
		"video_input": false, "audio_transcription": false,
		"history": false, "stop": false, "reset": false, "multi_conversation": false,
		"realtime_resume": false, "realtime_replay": false, "realtime_wss": false,
	}
}

func publicAIDocumentationLinks() map[string]string {
	return map[string]string{
		"version":   publicAPIDocumentationVersion,
		"endpoints": "https://chat.harmonika.id/v1/public-ai/endpoints",
		"guide":     "https://chat.harmonika.id/v1/public-ai/guide.txt",
	}
}

func (a *app) publicAIEndpointsAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		jsonOut(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "method_not_allowed"})
		return
	}
	tok, ok := a.requirePublicAIToken(w, r)
	if !ok {
		return
	}
	a.logPublicAIRequest(tok, r, "")
	w.Header().Set("Cache-Control", "no-store")
	jsonOut(w, http.StatusOK, map[string]any{
		"ok": true, "object": "endpoint_catalog", "version": publicAPIDocumentationVersion,
		"base_url": "https://chat.harmonika.id/v1", "authentication": "Bearer public API token",
		"features": publicAITokenFeatures(tok), "endpoints": publicAPIEndpoints(),
		"documentation": publicAIDocumentationLinks(),
		"note":          "Token publik bukan token member APK atau sesi admin. Katalog ini tidak membuka akses data pelanggan, billing, CS, atau browser internal.",
	})
}

func (a *app) publicAIGuideDownload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		jsonOut(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "method_not_allowed"})
		return
	}
	tok, ok := a.requirePublicAIToken(w, r)
	if !ok {
		return
	}
	a.logPublicAIRequest(tok, r, "")
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="harmonika-ai-api-endpoints.txt"`)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_, _ = fmt.Fprint(w, publicAIEndpointGuide("YOUR_API_KEY"))
}

func publicAITelegramKeyboard() map[string]any {
	return map[string]any{"inline_keyboard": [][]map[string]string{
		{{"text": "➕ Buat Token API", "callback_data": "create_token"}, {"text": "📋 List Token", "callback_data": "list_tokens"}},
		{{"text": "📄 Panduan API terbaru", "callback_data": "api_guide"}},
	}}
}

func publicAIGuideCommand(text string) bool {
	parts := strings.Fields(strings.TrimSpace(text))
	if len(parts) == 0 {
		return false
	}
	command := strings.SplitN(parts[0], "@", 2)[0]
	return command == "api_guide" || command == "/panduan" || command == "/endpoints" || command == "/api"
}

func publicAIEndpointGuide(apiToken string) string {
	key := strings.TrimSpace(apiToken)
	if key == "" {
		key = "YOUR_API_KEY"
	}
	var b strings.Builder
	fmt.Fprintf(&b, "HARMONIKA AI - PANDUAN INTEGRASI API\nVersi: %s\n\n", publicAPIDocumentationVersion)
	fmt.Fprintf(&b, "BASE URL\nhttps://chat.harmonika.id/v1\n\nAUTENTIKASI\nAuthorization: Bearer %s\n\n", key)
	b.WriteString("Token ini untuk API publik server-to-server. Jangan pasang token di JavaScript publik/APK atau repository. Gunakan backend Anda sebagai perantara. Token member APK dan sesi admin merupakan autentikasi terpisah.\n\nENDPOINT PUBLIK AKTIF\n")
	for _, ep := range publicAPIEndpoints() {
		fmt.Fprintf(&b, "%-4s %-32s %s [%s]\n", ep.Method, ep.Path, ep.Description, ep.Response)
	}
	b.WriteString(`
FITUR TOKEN PUBLIK
- Chat pengetahuan umum, ide/konten, coding dan analisis teks.
- Chat JSON non-stream dan streaming SSE.
- Orchestration/fallback internal otomatis bila tersedia; tidak perlu memilih engine.
- Informasi web dapat dibantu backend bila jalurnya tersedia. Flag bukan jaminan setiap jawaban melakukan pencarian atau memiliki sumber terstruktur.
- Katalog endpoint dan panduan TXT dapat diunduh memakai token yang sudah ada.

BELUM TERBUKA UNTUK TOKEN PUBLIK
- Upload PDF/dokumen/gambar, analisis video/audio, generate/edit gambar dan file hasil.
- Riwayat permanen server, reset/stop endpoint khusus, multi-conversation, replay/resume dan WebSocket.
- Token ini tidak memberi akses data pelanggan, ERP, billing, pembayaran, CS, browser atau akun internal.
- Jangan mengirim attachment_ids, Base64 atau image_url ke chat publik. Content multimodal ditolak, bukan dianggap telah dianalisis.
- Selalu baca features di GET /v1/public-ai/capabilities. Aktifkan UI hanya jika flag fitur yang sesuai true.

1. CHAT JSON NON-STREAM
export HARMONIKA_API_KEY='`)
	b.WriteString(key)
	b.WriteString(`'
curl --fail-with-body https://chat.harmonika.id/v1/chat/completions \
  -H "Authorization: Bearer $HARMONIKA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"harmonika-ai","stream":false,"session_id":"situsA-user123-chat1","messages":[{"role":"user","content":"Buat 3 ide promosi usaha makanan rumahan"}]}'

Response JSON: object="chat.completion"; jawaban ada di choices[0].message.content.
Contoh: {"object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"..."},"finish_reason":"stop"}]}
ID, timestamp dan metadata lainnya dapat ditambahkan server.

2. CHAT SSE KOMPATIBEL
curl -N https://chat.harmonika.id/v1/chat/completions \
  -H "Authorization: Bearer $HARMONIKA_API_KEY" \
  -H "Content-Type: application/json" -H "Accept: text/event-stream" \
  -d '{"model":"harmonika-ai","stream":true,"messages":[{"role":"user","content":"Jelaskan apa itu router secara singkat"}]}'

data: {"object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"..."},"finish_reason":null}]}
data: {"object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
data: [DONE]

3. CHAT NATIVE SSE
curl -N https://chat.harmonika.id/v1/public-ai/chat/messages \
  -H "Authorization: Bearer $HARMONIKA_API_KEY" \
  -H "Content-Type: application/json" -H "Accept: text/event-stream" \
  -d '{"message":"Halo Harmonika AI","session_id":"situsA-user123-chat1"}'

data: {"type":"started","session_id":"..."}
data: {"type":"delta","text":"..."}
data: {"type":"final","ok":true}

Native selalu SSE meskipun stream tidak dikirim. session_id pada event started saat ini adalah ID respons, jangan menimpanya ke ID percakapan aplikasi Anda.

PAYLOAD DAN KONTEKS
- Skill SEO read-only dan workflow engineering dipilih otomatis dari permintaan chat dan konteks yang dikirim. Tidak perlu memilih skill atau mengganti key Telegram. Hasil audit berasal dari tool, bukan perkiraan model.
- Engineering: review kode/diff, debug log, rencana pengujian dan dokumentasi dari bukti yang dikirim. Tidak perlu memilih skill. Hasil worker tersedia di harmonika.reports; untuk SSE ada pada chunk terminal.
- Engineering tidak membaca repository privat atau menjalankan kode/test. Nomor baris snippet merujuk potongan yang dikirim, bukan klaim membaca file disk. Konteks otomatis maksimal 240 KiB.
- Automatic SEO tidak mengaktifkan shell/file client, browser privat, screenshot visual/drift, atau koneksi Search Console/GA4. Data Google memerlukan koneksi akun terotorisasi.
- /chat/completions: messages wajib array role/content teks. Kirim riwayat relevan sendiri untuk percakapan lanjutan. session_id, external_user_id dan user merupakan identifier opsional, bukan jaminan penyimpanan riwayat server.
- /public-ai/chat/messages: message wajib teks; session_id atau external_user_id opsional. Endpoint ini tidak menerima array history.
- Pisahkan identifier per aplikasi/pengguna/percakapan. Hindari nomor telepon atau data pribadi sebagai identifier.
- Gunakan alias harmonika-ai atau biarkan model kosong. Kompatibilitas bukan seluruh fitur/protokol provider lain; tool/function calling, JSON schema, max_tokens/temperature khusus, token usage dan sources terstruktur belum dijamin.
- Pembatalan client: batalkan HTTP request/AbortController. Ini bukan jaminan pekerjaan upstream langsung berhenti atau pemakaian kuota dibatalkan.
- Koneksi putus sebelum terminal event = hasil belum terkonfirmasi selesai. Jangan menandainya sukses atau auto-retry buta. Endpoint publik belum menjamin idempotency/replay.

BATAS DAN ERROR
- Maksimum 102.400 BYTE UTF-8 (100 KiB) per message/content teks; bukan karakter. Payload /chat/completions maksimal 2 MiB.
- Kuota default 1.000 request per token pada jendela 24 jam bergulir, bukan reset otomatis pukul 00.00. Nilai token dapat dikonfigurasi admin.
- GET metadata/panduan dan request terautentikasi yang ditolak validasi juga tercatat sebagai request. Cache metadata secara wajar. Reservasi kuota atomik per token mencegah request paralel melampaui kuota.
- Tier internal (hai_internal_) hanya diterbitkan admin server: default 100.000 request/24 jam, tools/function calling tersedia. Unlimited hanya sah pada tier internal; token publik tidak boleh unlimited.
- Internal tool calling: kirim tools, tool_choice (auto/none/required/function), messages assistant.tool_calls dan tool.tool_call_id. SSE meneruskan delta.tool_calls dan finish_reason=tool_calls. Client wajib menjalankan tool dengan persetujuan/sandbox sendiri lalu mengirim hasilnya. API tidak mengeksekusi command atau mengedit file client.
- 400 invalid_json / messages_required / message_required: koreksi payload.
- 401 missing_api_key / invalid_api_key: periksa token; token revoked juga ditolak.
- 413 message_too_large: pendekkan teks. 422 unsupported_media: input ini belum didukung token publik.
- 429 quota_exceeded: hentikan retry cepat; tunggu jendela kuota atau hubungi pengelola.
- 502 engine_error: backend belum menghasilkan jawaban; lakukan retry terbatas dengan jeda. Fallback tidak menjamin selalu berhasil.
- 503 quota_unavailable: verifikasi kuota gagal; akses ditahan sementara, bukan dianggap kuota tak terbatas.
- Jika stream sudah mengirim teks lalu gagal: terima error stream_interrupted, bukan final sukses. Jangan sambung otomatis jawaban kedua ke bubble yang sama.
- Periksa HTTP status dan Content-Type sebelum parsing: SSE bukan satu dokumen JSON. Baca tiap frame data:, lalu JSON.parse isinya; [DONE] adalah penanda, bukan JSON.
- Bentuk error kompatibel: {"error":{"message":"...","type":"invalid_request_error","code":"invalid_api_key"}}. Jalur native juga dapat mengembalikan error sebagai string; tangani kedua bentuk.

KATALOG FITUR PRODUK LAIN - 
CODING WORKSPACE (PHASE 1 + PHASE 2)
- Gunakan Bearer key yang sama. Workspace privat dibedakan per token, bukan per IP.
- GET /v1/coding/capabilities memuat schema/readiness operasi coding yang aktif (18 dasar, hingga 38 saat Phase2 aktif).
- POST /v1/coding/workspaces body {"name":"Demo","permissions":{"write":true,"execute":true,"git_commit":false}}.
- Chat coding: POST /v1/chat/completions dengan workspace_id, messages, stream dan header Idempotency-Key UUID. Skill dipilih otomatis.
- File proyek lokal tidak otomatis tersalin ke server. Transfer hanya source yang dimaksud, tanpa credential.
- Invoke eksplisit: POST /v1/coding/workspaces/{id}/invoke body {"skill_id":"code.write","input":{"path":"main.py","content":"print(42)"}} dan Idempotency-Key.
- exec.*, test.run, git.commit mengembalikan approval_required: BELUM dijalankan.
- Tinjau preview lalu POST /v1/coding/jobs/{id}/approve body {"decision":"approve","input_hash":"HASH_DARI_JOB"}. Hanya aksi persetujuan client, bukan model AI, yang memanggil endpoint ini.
- Approval kedaluwarsa 5 menit; perubahan workspace membatalkannya. Retry input sama memakai key sama; input berbeda memakai key baru.
- GET /v1/coding/jobs/{id}, /events; POST /stop untuk recovery/Stop. Event mendukung after_sequence/Last-Event-ID. Recovery dan Stop tidak memakai kuota mutasi.
- Phase2 hanya aktif jika features.coding_phase2=true; schema/dukungan bahasa/paket dibaca dari /v1/coding/capabilities.
- pkg.install hanya paket offline ber-versi yang diizinkan, bukan arbitrary internet install. Debugger Python hanya debug_session_id milik workspace; tidak menerima PID host.
- Continue/next/inspect memakai /v1/coding/debug/sessions/{id}/actions dengan Idempotency-Key, Stop melalui /stop; sesi punya TTL.
- Eksekusi dalam container tanpa jaringan, tanpa akses host/proyek lain. Batas 256 file, 512 KiB/file,16 MiB/workspace,120 detik/command,2 proses global. Tidak ada package install/deploy otomatis.

AUTENTIKASI TERPISAH
Daftar berikut bukan izin akses dengan token Telegram ini. Jangan mengubah base URL publik menjadi /member-ai lalu berharap tokennya berlaku.

A. APK member - Bearer token login pelanggan member.harmonika.id
GET    /v1/member-ai/me
GET    /v1/member-ai/capabilities
GET    /v1/member-ai/chat/session
DELETE /v1/member-ai/chat/session
GET    /v1/member-ai/chat/messages?limit=50&before_id=...
POST   /v1/member-ai/chat/messages          JSON {"message":"...","attachment_ids":["file_..."]}; SSE
POST   /v1/member-ai/chat/stop
POST   /v1/member-ai/files                  multipart/form-data field "file"
POST   /v1/member-ai/images/generations     hanya jika capability gambar aktif
GET    /v1/member-ai/files/{file_id}/preview
GET    /v1/member-ai/files/{file_id}/download

Upload: maksimal 10 MiB/file, maksimal 3 file/pesan. PDF/TXT/CSV/DOCX/XLSX sesuai capabilities; PNG/JPEG/WebP hanya jika image_input=true. Hanya attachment_ids milik akun yang sama dan berstatus ready. Tidak memakai URL publik permanen.
Generate: prompt,size,count=1; reference_file_ids maksimal 3 image milik akun yang sama jika jalurnya siap. Baca response aktual; jangan mengasumsikan kontrak job/SSE/retry dari rencana lama sudah lengkap. Endpoint image job events/stop/retry dan replay/WSS belum dijamin aktif.
Jangan gunakan /v1/mobile/auth/login untuk APK member yang sudah punya token pelanggan.

B. Multimodal admin/integrasi privat - bukan token publik
POST /v1/vision/analyze       image_base64 atau image_url + prompt; optional profile_id/task_id
POST /v1/video/analyze        frames_base64[] atau video_url + caption + prompt
POST /v1/audio/transcriptions audio_base64 atau audio_url + language
Jalur ini membutuhkan otorisasi admin/integrasi yang sesuai. Endpoint dapat berstatus degraded; jangan mengklaim file sudah dibaca atau audio ditranskripsi jika hasil tidak menyatakan analisis berhasil. Endpoint hidup tidak berarti model/provider siap.

BOT TELEGRAM
- Buat Token API: menghasilkan token baru dan TXT berisi token tersebut; simpan dengan aman.
- List Token: melihat ID/status token. Token lama tidak dapat ditampilkan ulang karena disimpan sebagai hash.
- Panduan API terbaru atau /panduan: unduh panduan terkini tanpa membuat/mengganti token. Ganti YOUR_API_KEY dengan token Anda sendiri.
- Untuk mencabut token milik chat ini, kirim revoke_ID, contoh revoke_123. Jangan membuat token baru hanya untuk memperbarui panduan.

CHECKLIST INSTALASI
1. Simpan key pada environment backend, bukan URL/query-string atau frontend.
2. Panggil capabilities dan katalog endpoint memakai Bearer.
3. Uji satu chat JSON, lalu SSE sampai terminal event.
4. Simpan history aplikasi sendiri dan pisahkan pengguna/percakapan.
5. Tangani timeout/401/413/422/429/502 tanpa mengirim ulang pembayaran/tindakan bisnis.
6. Jangan mengaktifkan Upload/Gambar/Audio/ERP hanya karena endpoint serupa ada di produk lain.
`)
	// Raw literals above are intended to remain copy/pasteable shell and JSON.
	return strings.ReplaceAll(b.String(), `\"`, `"`)
}

func publicAIChatInputError(input publicAIChatCompletionRequest) (int, string) {
	if len(input.AttachmentIDs) > 0 {
		return http.StatusUnprocessableEntity, "unsupported_media"
	}
	for _, m := range input.Messages {
		if text, ok := m.Content.(string); ok && len([]byte(text)) > publicAIMaxMessageBytes {
			return 413, "message_too_large"
		}
		for _, call := range m.ToolCalls {
			if len([]byte(call.Function.Arguments)) > publicAIMaxMessageBytes {
				return 413, "message_too_large"
			}
		}
		if m.Content != nil {
			switch m.Content.(type) {
			case string, []any:
			default:
				return http.StatusUnprocessableEntity, "unsupported_media"
			}
		}
		if parts, ok := m.Content.([]any); ok {
			for _, part := range parts {
				p, ok := part.(map[string]any)
				if !ok {
					continue
				}
				kind, _ := p["type"].(string)
				if kind != "" && kind != "text" && kind != "input_text" {
					return http.StatusUnprocessableEntity, "unsupported_media"
				}
				for _, field := range []string{"image_url", "input_image", "input_audio", "audio_url", "video_url", "file", "file_id"} {
					if _, present := p[field]; present {
						return http.StatusUnprocessableEntity, "unsupported_media"
					}
				}
			}
		}
		if len([]byte(publicAIContentText(m.Content))) > publicAIMaxMessageBytes {
			return http.StatusRequestEntityTooLarge, "message_too_large"
		}
	}
	return 0, ""
}
