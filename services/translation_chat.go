package main

// Explicit translation chat bypasses general chat policy, tool dispatch and
// content sampling. Authentication and quota admission already happened.
import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

var translationChatColon = regexp.MustCompile(`(?is)^(?:tolong[ \t]+|please[ \t]+)?(?:terjemahkan|translate)[ \t]+(?:ke(?:[ \t]+dalam)?|into|to)[ \t]+(?:bahasa[ \t]+)?([\p{L}\p{M}][\p{L}\p{M} 0-9_()\-]{0,79})[ \t]*:[ \t]?(.+)$`)
var translationChatQuoted = regexp.MustCompile(`(?is)^(?:tolong[ \t]+|please[ \t]+)?(?:terjemahkan|translate)[ \t]+(.+?)[ \t]+(?:ke(?:[ \t]+dalam)?|into|to)[ \t]+(?:bahasa[ \t]+)?([\p{L}\p{M}][\p{L}\p{M} 0-9_()\-]{0,79})[.!]?$`)
var translationChatCSContext = regexp.MustCompile(`(?i)customer[ -](?:service|support)|layanan pelanggan|website-cs|cs-autoreply`)

func translationChatIntent(prompt string) (text, target string, ok bool) {
	if !utf8.ValidString(prompt) {
		return "", "", false
	}
	prompt = strings.TrimLeftFunc(prompt, unicode.IsSpace)
	if match := translationChatColon.FindStringSubmatch(prompt); match != nil {
		if strings.TrimSpace(match[2]) != "" {
			return match[2], strings.TrimSpace(match[1]), true
		}
	}
	if match := translationChatQuoted.FindStringSubmatch(strings.TrimSpace(prompt)); match != nil {
		quoted := []rune(strings.TrimSpace(match[1]))
		if len(quoted) >= 3 {
			pairs := map[rune]rune{'"': '"', '\'': '\'', '“': '”', '‘': '’', '«': '»'}
			if end, found := pairs[quoted[0]]; found && quoted[len(quoted)-1] == end {
				text = string(quoted[1 : len(quoted)-1])
				if strings.TrimSpace(text) != "" {
					return text, strings.TrimSpace(match[2]), true
				}
			}
		}
	}
	return "", "", false
}

func translationChatCandidate(r *http.Request, input publicAIChatCompletionRequest) (string, string, bool) {
	if len(input.Tools) != 0 || input.ToolChoice != nil || input.Legal || input.Telco || input.WorkspaceID != "" || len(input.AttachmentIDs) != 0 || len(input.Messages) == 0 {
		return "", "", false
	}
	client := strings.ToLower(strings.TrimSpace(r.Header.Get("X-Harmonika-Client")))
	if client == "internal-agent" || client == "customer-agent" || client == "cs" || client == "pro-cs" || strings.HasPrefix(client, "website-cs-") {
		return "", "", false
	}
	for _, message := range input.Messages {
		role := strings.ToLower(strings.TrimSpace(message.Role))
		if role == "tool" || role == "function" || role == "cs" || len(message.ToolCalls) != 0 || message.ToolCallID != "" {
			return "", "", false
		}
		if role == "system" || role == "developer" {
			if content, ok := message.Content.(string); ok && translationChatCSContext.MatchString(content) {
				return "", "", false
			}
		}
	}
	latest := input.Messages[len(input.Messages)-1]
	if strings.ToLower(strings.TrimSpace(latest.Role)) != "user" {
		return "", "", false
	}
	text, ok := latest.Content.(string)
	if !ok { // Media and mixed content keep their existing route.
		return "", "", false
	}
	return translationChatIntent(text)
}

// This internal writer reuses the bounded worker gateway without another HTTP
// admission, credential forwarding or public authentication/quota reservation.
type translationChatBuffer struct {
	header http.Header
	status int
	body   bytes.Buffer
}

func (b *translationChatBuffer) Header() http.Header { return b.header }
func (b *translationChatBuffer) WriteHeader(status int) {
	if b.status == 0 {
		b.status = status
	}
}
func (b *translationChatBuffer) Write(data []byte) (int, error) {
	if b.status == 0 {
		b.status = http.StatusOK
	}
	if b.body.Len()+len(data) > translationResponseLimit {
		return 0, io.ErrShortBuffer
	}
	return b.body.Write(data)
}

// Call after publicAIChatInputError/messages validation and before
// qualityChatInput or any general chat routing. tok is the admitted identity.
func (a *app) publicAITranslationChat(w http.ResponseWriter, r *http.Request, tok publicAIToken, input publicAIChatCompletionRequest) bool {
	return serveTranslationChat(translationGateway, w, r, tok, input)
}

func serveTranslationChat(gateway *translationWorkerGateway, w http.ResponseWriter, r *http.Request, tok publicAIToken, input publicAIChatCompletionRequest) bool {
	text, target, ok := translationChatCandidate(r, input)
	if !ok {
		return false
	}
	if tok.ID <= 0 {
		translationChatFailure(w, http.StatusUnauthorized, "unauthorized")
		return true
	}
	body, err := json.Marshal(map[string]string{"text": text, "target": target, "source": "auto"})
	if err != nil {
		translationChatFailure(w, http.StatusBadRequest, "invalid_request_body")
		return true
	}
	internal := &http.Request{
		Method: http.MethodPost, URL: &url.URL{Path: "/v1/translate"},
		Header: make(http.Header), Body: io.NopCloser(bytes.NewReader(body)), ContentLength: int64(len(body)),
	}
	internal = internal.WithContext(r.Context())
	buffer := &translationChatBuffer{header: make(http.Header)}
	serveTranslationTools(gateway, tok.ID, buffer, internal)
	if buffer.status != http.StatusOK {
		var failure struct {
			Error string `json:"error"`
		}
		_ = json.Unmarshal(buffer.body.Bytes(), &failure)
		translationChatFailure(w, buffer.status, failure.Error)
		return true
	}
	var result struct {
		OK               bool    `json:"ok"`
		Text             string  `json:"text"`
		Source           string  `json:"source"`
		Target           string  `json:"target"`
		DetectedLanguage *string `json:"detected_language"`
		NeedsReview      *bool   `json:"needs_review"`
		Quality          string  `json:"quality"`
	}
	if json.Unmarshal(buffer.body.Bytes(), &result) != nil || !result.OK || result.Text == "" || result.NeedsReview == nil || result.Target == "" {
		translationChatFailure(w, http.StatusBadGateway, "translation_invalid_response")
		return true
	}
	metadata := map[string]any{
		"source": result.Source, "target": result.Target, "detected_language": result.DetectedLanguage,
		"needs_review": *result.NeedsReview, "quality": result.Quality,
	}
	if *result.NeedsReview {
		result.Text += "\n\nCatatan: terjemahan ini perlu ditinjau, terutama dialek dan tingkat tutur."
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		translationChatFailure(w, 503, "translation_unavailable")
		return true
	}
	id, created := "chatcmpl_translation_"+hex.EncodeToString(nonce[:]), time.Now().Unix()
	// This model label identifies the service, never an unverified model vendor.
	model := "harmonika-translation"
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("X-Harmonika-Translation", "v1")
	if !input.Stream {
		jsonOut(w, http.StatusOK, map[string]any{
			"id": id, "object": "chat.completion", "created": created, "model": model,
			"choices":               []map[string]any{{"index": 0, "message": map[string]any{"role": "assistant", "content": result.Text}, "finish_reason": "stop"}},
			"harmonika_translation": metadata,
		})
		return true
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	writeChunk := func(delta map[string]any, finish any) bool {
		data, err := json.Marshal(map[string]any{
			"id": id, "object": "chat.completion.chunk", "created": created, "model": model,
			"choices":               []map[string]any{{"index": 0, "delta": delta, "finish_reason": finish}},
			"harmonika_translation": metadata,
		})
		if err != nil {
			return false
		}
		_, err = fmt.Fprintf(w, "data: %s\n\n", data)
		if flush, ok := w.(http.Flusher); ok {
			flush.Flush()
		}
		return err == nil
	}
	if !writeChunk(map[string]any{"role": "assistant", "content": result.Text}, nil) {
		return true
	}
	if !writeChunk(map[string]any{}, "stop") {
		return true
	}
	_, _ = io.WriteString(w, "data: [DONE]\n\n")
	if flush, ok := w.(http.Flusher); ok {
		flush.Flush()
	}
	return true
}

func translationChatFailure(w http.ResponseWriter, status int, code string) {
	message, kind := "Layanan terjemahan belum tersedia. Silakan coba lagi nanti.", "server_error"
	switch code {
	case "unsupported_language":
		status, kind, message = 422, "invalid_request_error", "Bahasa tujuan belum terdaftar pada layanan terjemahan."
	case "credential_literal_denied":
		status, code, kind, message = 422, "translation_input_denied", "invalid_request_error", "Hapus kredensial rahasia dari teks sebelum menerjemahkan."
	case "translation_busy":
		status, kind, message = 429, "rate_limit_error", "Layanan terjemahan sedang sibuk. Silakan coba lagi nanti."
	case "translation_timeout":
		status, message = 504, "Batas waktu terjemahan terlampaui."
	case "input_too_large", "text_too_large", "translation_request_too_large":
		status, code, kind, message = 413, "translation_request_too_large", "invalid_request_error", "Teks terlalu besar untuk satu permintaan terjemahan."
	case "unauthorized":
		status, kind, message = 401, "invalid_request_error", "Identitas API belum terverifikasi."
	default:
		code = "translation_unavailable"
		if status != 502 && status != 503 && status != 504 {
			status = 502
		}
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	jsonOut(w, status, map[string]any{"error": map[string]any{"message": message, "type": kind, "code": code}})
}
