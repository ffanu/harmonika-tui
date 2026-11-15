package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type qualityTestSink struct{ events []qualityEvent }

func (s *qualityTestSink) Submit(e qualityEvent) { s.events = append(s.events, e) }
func qualityTestRequest() *http.Request {
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"messages":[{"role":"system","content":"PRIVATE-SYSTEM"},{"role":"user","content":"visible question"}]}`))
	r.Header.Set("Content-Type", "application/json")
	return r
}

func TestQualityJSONPreservedAndIdentityTrusted(t *testing.T) {
	sink := &qualityTestSink{}
	payload := `{"choices":[{"message":{"content":"visible answer","reasoning_content":"PRIVATE-REASONING"}}],"usage":{"prompt_tokens":7,"completion_tokens":3}}`
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, 7)
		qualityQuestion(r, "test-session", "latest question")
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(payload))
	}))
	r := qualityTestRequest()
	r.Header.Set("X-Harmonika-Principal", "api-token:999")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Body.String() != payload || len(sink.events) != 1 {
		t.Fatal("response changed or missing event")
	}
	e := sink.events[0]
	if e.TokenID != 7 || e.Question != "latest question" || e.Answer != "visible answer" || !e.Complete || *e.InputTokens != 7 {
		t.Fatal("incorrect capture")
	}
	raw, _ := json.Marshal(e)
	if bytes.Contains(raw, []byte("PRIVATE")) {
		t.Fatal("hidden content captured")
	}
	if string(raw) == "" || e.Steps == nil {
		t.Fatal("invalid steps contract")
	}
}
func TestQualityNoAuthenticatedIdentityNoEvent(t *testing.T) {
	sink := &qualityTestSink{}
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(401) }))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
	if len(sink.events) != 0 {
		t.Fatal("untrusted identity logged")
	}
}
func TestQualityFragmentedUTF8SSEAndToolOrigin(t *testing.T) {
	sink := &qualityTestSink{}
	payload := "data: {\"choices\":[{\"delta\":{\"content\":\"Halo مرحبا\"}}]}\r\n\r\ndata: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"function\":{\"name\":\"web.search\",\"arguments\":\"PRIVATE-ARGS\"}}]}}]}\n\ndata: [DONE]\n\n"
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, 7)
		qualityRecordStep(r.Context(), "ocr.document", time.Now(), false)
		w.Header().Set("Content-Type", "text/event-stream")
		for _, b := range []byte(payload) {
			w.Write([]byte{b})
			w.(http.Flusher).Flush()
		}
	}))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, qualityTestRequest())
	e := sink.events[0]
	if w.Body.String() != payload || e.Answer != "Halo مرحبا" || !e.Complete || len(e.Steps) != 2 {
		t.Fatal("SSE fragmented capture incorrect")
	}
	if e.Steps[0].Origin != "server_executor" || e.Steps[1].Origin != "model_requested" {
		t.Fatal("evidence conflated")
	}
	raw, _ := json.Marshal(e)
	if bytes.Contains(raw, []byte("PRIVATE-ARGS")) {
		t.Fatal("arguments captured")
	}
}
func TestQualitySSEErrorEvenHTTP200(t *testing.T) {
	sink := &qualityTestSink{}
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, 7)
		w.Header().Set("Content-Type", "text/event-stream")
		w.Write([]byte("data: {\"error\":{\"message\":\"PRIVATE-ERROR\"}}\n\ndata: [DONE]\n\n"))
	}))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
	e := sink.events[0]
	if e.Complete || e.ErrorCode == "none" || e.Answer != "" {
		t.Fatal("false success")
	}
}
func TestQualityIncompleteStreamAndNoResponseReplay(t *testing.T) {
	sink := &qualityTestSink{}
	calls := 0
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		qualityBind(r, 7)
		w.Header().Set("Content-Type", "text/event-stream")
		w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n"))
	}))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
	if calls != 1 || sink.events[0].Complete || sink.events[0].ErrorCode != "stream_incomplete" {
		t.Fatal("incomplete stream claimed completed")
	}
}
func TestQualityLongQuestionHookAndBoundedAnswer(t *testing.T) {
	sink := &qualityTestSink{}
	body := `{"history":"` + strings.Repeat("x", 300000) + `"}`
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, 7)
		var v any
		json.NewDecoder(r.Body).Decode(&v)
		qualityQuestion(r, "s", "latest question")
		w.Header().Set("Content-Type", "text/event-stream")
		raw, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"delta": map[string]any{"content": strings.Repeat("z", 70000)}}}})
		w.Write(append(append([]byte("data: "), raw...), []byte("\n\ndata: [DONE]\n\n")...))
	}))
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	h.ServeHTTP(httptest.NewRecorder(), r)
	e := sink.events[0]
	if !e.CaptureTruncated || len(e.Answer) > 65536 || e.Question != "latest question" {
		t.Fatal("unbounded capture or latest question lost")
	}
}
func TestQualityQueueSaturationDoesNotBlockAPI(t *testing.T) {
	s := &qualitySender{queue: make(chan qualityEvent, 1)}
	s.Submit(qualityEvent{})
	start := time.Now()
	s.Submit(qualityEvent{})
	if time.Since(start) > time.Second || s.dropped.Load() != 1 {
		t.Fatal("blocking logging")
	}
}
func TestQualityCancellationNotSuccessful(t *testing.T) {
	sink := &qualityTestSink{}
	ctx, cancel := context.WithCancel(context.Background())
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { qualityBind(r, 7); cancel(); w.WriteHeader(200) }))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest().WithContext(ctx))
	if sink.events[0].ErrorCode != "cancelled" {
		t.Fatal("cancellation missing")
	}
}
func TestQualityPanicIsRecordedWithoutChangingRecovery(t *testing.T) {
	sink := &qualityTestSink{}
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { qualityBind(r, 7); panic("PRIVATE-PANIC") }))
	defer func() {
		if recover() != "PRIVATE-PANIC" {
			t.Fatal("panic semantics changed")
		}
		if len(sink.events) != 1 || sink.events[0].Complete || sink.events[0].Status != 500 {
			t.Fatal("panic not recorded")
		}
		raw, _ := json.Marshal(sink.events[0])
		if bytes.Contains(raw, []byte("PRIVATE-PANIC")) {
			t.Fatal("panic text leaked")
		}
	}()
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
}
func TestQualityBadPayloadAndFinalSnapshot(t *testing.T) {
	sink := &qualityTestSink{}
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, 7)
		w.Header().Set("Content-Type", "text/event-stream")
		w.Write([]byte("data: {\"type\":\"delta\",\"text\":\"visible\"}\n\ndata: {\"type\":\"final\",\"answer\":\"visible\"}\n\n"))
	}))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
	if sink.events[0].Answer != "visible" {
		t.Fatal("final snapshot duplicated")
	}
	sink = &qualityTestSink{}
	h = qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, 7)
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte("invalid-json"))
	}))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
	if sink.events[0].Complete || sink.events[0].ErrorCode != "bad_payload" {
		t.Fatal("bad payload accepted")
	}
}

func TestQualityBackgroundStateAndSplitToolNames(t *testing.T) {
	sink := &qualityTestSink{}
	h := qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, 7)
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"job_id":"synthetic-job","status":"failed"}`))
	}))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
	e := sink.events[0]
	if !e.Complete || len(e.Steps) != 1 || e.Steps[0].Kind != "job.status" || e.Steps[0].Status != "failed" || e.Steps[0].Origin != "server_reported" {
		t.Fatal("background job conflated with HTTP completion")
	}
	sink = &qualityTestSink{}
	h = qualityMiddleware(sink, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, 7)
		w.Header().Set("Content-Type", "text/event-stream")
		for _, name := range []string{"code.", "search"} {
			raw, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"delta": map[string]any{"tool_calls": []any{map[string]any{"index": 0, "function": map[string]any{"name": name}}}}}}})
			w.Write(append(append([]byte("data: "), raw...), []byte("\n\n")...))
		}
		w.Write([]byte("data: [DONE]\n\n"))
	}))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
	if len(sink.events[0].Steps) != 1 || sink.events[0].Steps[0].SkillID != "code.search" {
		t.Fatal("one fragmented tool call counted as separate actions")
	}
}
