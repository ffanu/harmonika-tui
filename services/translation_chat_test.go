package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestTranslationChatExplicitIntent(t *testing.T) {
	for _, tc := range []struct{ prompt, text, target string }{
		{"Terjemahkan ke bahasa Jawa: OpenAI dan Google 123", "OpenAI dan Google 123", "Jawa"},
		{"translate to Japanese: Hello 世界", "Hello 世界", "Japanese"},
		{"Please translate into Japanese:\nHello\nworld", "\nHello\nworld", "Japanese"},
		{"Tolong terjemahkan ke dalam bahasa Jawa krama: Sugeng enjing", "Sugeng enjing", "Jawa krama"},
		{`Terjemahkan "hello" ke bahasa Jepang`, "hello", "Jepang"},
		{`translate 'Hello OpenAI' to Japanese.`, "Hello OpenAI", "Japanese"},
		{"terjemahkan “ hello 👋 ” ke bahasa Jawa!", " hello 👋 ", "Jawa"},
		{"translate to English: Apa kabar?", "Apa kabar?", "English"},
	} {
		t.Run(tc.prompt, func(t *testing.T) {
			text, target, ok := translationChatIntent(tc.prompt)
			if !ok || text != tc.text || target != tc.target {
				t.Fatalf("intent=%q %q %v", text, target, ok)
			}
		})
	}
	for _, prompt := range []string{
		"Apa arti translate?", "Bisakah terjemahkan ke bahasa Jawa: hello", "Can you translate to Japanese: hello",
		"Bagaimana cara translate to Japanese: hello", "Jangan terjemahkan ke bahasa Jawa: hello",
		"Contoh: translate to Japanese: hello", "translate to Japanese", "translate to Japanese:",
		"translate to Japanese:  ", "translate hello to Japanese", "translate \"\" to Japanese",
		"terjemahkan 'hello\" ke bahasa Jawa", "translate 'hello' to Japanese?", "translate to Japanese: \xff",
	} {
		if _, _, ok := translationChatIntent(prompt); ok {
			t.Errorf("nonimperative/ambiguous input routed: %q", prompt)
		}
	}
}

func translationChatTestInput(stream bool) publicAIChatCompletionRequest {
	return publicAIChatCompletionRequest{
		Model: "harmonika-ai", Stream: stream,
		Messages: []publicAIChatMessage{{Role: "system", Content: "Answer helpfully."}, {Role: "user", Content: "Terjemahkan ke bahasa Jawa: OpenAI dan Google 123"}},
	}
}

func TestTranslationChatKeepsToolAndCSRoutes(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(*publicAIChatCompletionRequest, *http.Request)
	}{
		{"tools", func(in *publicAIChatCompletionRequest, r *http.Request) { in.Tools = []publicAITool{{}} }},
		{"tool-choice", func(in *publicAIChatCompletionRequest, r *http.Request) { in.ToolChoice = "none" }},
		{"tool-history", func(in *publicAIChatCompletionRequest, r *http.Request) { in.Messages[0].ToolCallID = "synthetic" }},
		{"tool-role", func(in *publicAIChatCompletionRequest, r *http.Request) { in.Messages[0].Role = "TOOL" }},
		{"cs-role", func(in *publicAIChatCompletionRequest, r *http.Request) { in.Messages[0].Role = "cs" }},
		{"cs-header", func(in *publicAIChatCompletionRequest, r *http.Request) {
			r.Header.Set("X-Harmonika-Client", "website-cs-preview")
		}},
		{"customer-agent", func(in *publicAIChatCompletionRequest, r *http.Request) {
			r.Header.Set("X-Harmonika-Client", "customer-agent")
		}},
		{"cs-system", func(in *publicAIChatCompletionRequest, r *http.Request) {
			in.Messages[0].Content = "You are a customer service assistant."
		}},
		{"legal", func(in *publicAIChatCompletionRequest, r *http.Request) { in.Legal = true }},
		{"telco", func(in *publicAIChatCompletionRequest, r *http.Request) { in.Telco = true }},
		{"workspace", func(in *publicAIChatCompletionRequest, r *http.Request) { in.WorkspaceID = "synthetic" }},
		{"attachments", func(in *publicAIChatCompletionRequest, r *http.Request) { in.AttachmentIDs = []string{"synthetic"} }},
		{"last-assistant", func(in *publicAIChatCompletionRequest, r *http.Request) { in.Messages[1].Role = "assistant" }},
		{"last-media", func(in *publicAIChatCompletionRequest, r *http.Request) {
			in.Messages[1].Content = []any{map[string]any{"type": "text", "text": "translate to Japanese: Hello"}}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			in := translationChatTestInput(false)
			r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
			tc.change(&in, r)
			w := httptest.NewRecorder()
			if serveTranslationChat(nil, w, r, publicAIToken{ID: 42}, in) || w.Body.Len() != 0 {
				t.Fatal("existing route intercepted")
			}
		})
	}
}

func TestTranslationChatCompletionUsesWorkerAndVerifiedActor(t *testing.T) {
	var calls atomic.Int32
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != "/translate" || r.Header.Get("X-Harmonika-Translation-Actor") != "token:42" || r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" {
			t.Error("unsafe worker dispatch")
		}
		var in map[string]string
		if json.NewDecoder(r.Body).Decode(&in) != nil || len(in) != 3 || in["text"] != "OpenAI dan Google 123" || in["target"] != "Jawa" || in["source"] != "auto" {
			t.Error("wrong translation input")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"ok":true,"text":"OpenAI lan Google 123","source":"auto","target":"jv","detected_language":"id","needs_review":true,"quality":"model_self_estimated"}`)
	})
	r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	r.Header.Set("Authorization", "Bearer synthetic-public-token")
	r.Header.Set("Cookie", "synthetic=value")
	r.Header.Set("X-Harmonika-Translation-Actor", "token:999")
	w := httptest.NewRecorder()
	if !serveTranslationChat(gateway, w, r, publicAIToken{ID: 42}, translationChatTestInput(false)) {
		t.Fatal("explicit translation not handled")
	}
	var out struct {
		Object  string `json:"object"`
		Choices []struct {
			Message      struct{ Role, Content string }
			FinishReason string `json:"finish_reason"`
		}
		Metadata struct {
			NeedsReview bool   `json:"needs_review"`
			Target      string `json:"target"`
		} `json:"harmonika_translation"`
	}
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &out) != nil || out.Object != "chat.completion" || len(out.Choices) != 1 || out.Choices[0].Message.Content != "OpenAI lan Google 123" || out.Choices[0].Message.Role != "assistant" || out.Choices[0].FinishReason != "stop" || !out.Metadata.NeedsReview || out.Metadata.Target != "jv" {
		t.Fatalf("invalid completion: %d %s", w.Code, w.Body.String())
	}
	if calls.Load() != 1 || w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("dispatch repeated or result cacheable")
	}
}

func TestTranslationChatSSEContainsCompletionChunksAndReview(t *testing.T) {
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"ok":true,"text":"OpenAI lan Google 123\nSugeng enjing 👋","source":"auto","target":"jv","detected_language":"id","needs_review":true,"quality":"model_self_estimated"}`)
	})
	w := httptest.NewRecorder()
	if !serveTranslationChat(gateway, w, httptest.NewRequest("POST", "/v1/chat/completions", nil), publicAIToken{ID: 42}, translationChatTestInput(true)) {
		t.Fatal("not handled")
	}
	if w.Code != 200 || !strings.HasPrefix(w.Header().Get("Content-Type"), "text/event-stream") || !strings.HasSuffix(w.Body.String(), "data: [DONE]\n\n") || !w.Flushed {
		t.Fatal("invalid SSE transport")
	}
	var content, id string
	chunks := 0
	for _, line := range strings.Split(w.Body.String(), "\n") {
		if !strings.HasPrefix(line, "data: ") || line == "data: [DONE]" {
			continue
		}
		var event struct {
			ID, Object string
			Choices    []struct {
				Delta        struct{ Content string }
				FinishReason *string `json:"finish_reason"`
			}
			Metadata struct {
				NeedsReview bool `json:"needs_review"`
			} `json:"harmonika_translation"`
		}
		if json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event) != nil || event.Object != "chat.completion.chunk" || len(event.Choices) != 1 || !event.Metadata.NeedsReview {
			t.Fatal("invalid SSE event")
		}
		if id != "" && id != event.ID {
			t.Fatal("completion identity changed")
		}
		id = event.ID
		content += event.Choices[0].Delta.Content
		if chunks == 1 && (event.Choices[0].FinishReason == nil || *event.Choices[0].FinishReason != "stop") {
			t.Fatal("missing terminal chunk")
		}
		chunks++
	}
	if chunks != 2 || content != "OpenAI lan Google 123\nSugeng enjing 👋" {
		t.Fatal("SSE text changed")
	}
}

func TestTranslationChatFailureIsSafeAndDoesNotBecomeChatContent(t *testing.T) {
	for _, tc := range []struct {
		name       string
		status     int
		body, code string
		want       int
	}{
		{"unsupported", 422, `{"error":"unsupported_language"}`, "unsupported_language", 422},
		{"credential", 422, `{"error":"credential_literal_denied"}`, "translation_input_denied", 422},
		{"busy", 429, `{"error":"translation_busy"}`, "translation_busy", 429},
		{"timeout", 504, `{"error":"translation_timeout"}`, "translation_timeout", 504},
		{"raw", 500, `{"error":"SYNTHETIC_PRIVATE_PAYLOAD"}`, "translation_unavailable", 502},
		{"malformed-success", 200, `{"ok":true,"text":"SYNTHETIC_PRIVATE_PAYLOAD"}`, "translation_unavailable", 502},
	} {
		t.Run(tc.name, func(t *testing.T) {
			gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			})
			w := httptest.NewRecorder()
			serveTranslationChat(gateway, w, httptest.NewRequest("POST", "/v1/chat/completions", nil), publicAIToken{ID: 42}, translationChatTestInput(true))
			var out map[string]json.RawMessage
			if w.Code != tc.want || json.Unmarshal(w.Body.Bytes(), &out) != nil || out["error"] == nil || out["choices"] != nil || strings.Contains(w.Body.String(), "SYNTHETIC_PRIVATE") || !strings.Contains(w.Body.String(), tc.code) || strings.Contains(w.Header().Get("Content-Type"), "event-stream") {
				t.Fatalf("unsafe error: %d %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestTranslationChatRequiresAdmittedToken(t *testing.T) {
	var calls atomic.Int32
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1) })
	w := httptest.NewRecorder()
	if !serveTranslationChat(gateway, w, httptest.NewRequest("POST", "/v1/chat/completions", nil), publicAIToken{}, translationChatTestInput(false)) || w.Code != 401 || calls.Load() != 0 {
		t.Fatal("unverified actor admitted")
	}
}

func TestTranslationChatHookDoesNotReadmitOrLogContent(t *testing.T) {
	var captured bytes.Buffer
	previousLog := log.Writer()
	log.SetOutput(&captured)
	defer log.SetOutput(previousLog)
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"ok":true,"text":"SYNTHETIC_OUTPUT_SENTINEL","source":"auto","target":"jv","needs_review":true,"quality":"model_self_estimated"}`)
	})
	previousGateway := translationGateway
	translationGateway = gateway
	defer func() { translationGateway = previousGateway }()
	input := translationChatTestInput(false)
	input.Messages[1].Content = "Terjemahkan ke bahasa Jawa: SYNTHETIC_INPUT_SENTINEL"
	r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	// A second admission would reject this absent Authorization header in the
	// production handler. The supplied token was admitted by the outer handler.
	w := httptest.NewRecorder()
	if !(&app{}).publicAITranslationChat(w, r, publicAIToken{ID: 42}, input) || w.Code != 200 {
		t.Fatal("hook attempted another authentication or quota admission")
	}
	if !strings.Contains(w.Body.String(), "SYNTHETIC_OUTPUT_SENTINEL") || captured.Len() != 0 {
		t.Fatal("translation content was changed or logged")
	}
}
