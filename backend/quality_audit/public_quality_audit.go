package main

// This adapter observes only API requests authenticated by the gateway's own
// token validator. Authorization headers, tool arguments and hidden reasoning
// are never included in events. The collector owns consent and encryption.
import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

const qualityTextLimit = 65536

type qualityContextKey struct{}
type qualityStep struct {
	Kind      string `json:"kind"`
	SkillID   string `json:"skill_id"`
	Origin    string `json:"origin"`
	Status    string `json:"status"`
	LatencyMS int64  `json:"latency_ms"`
}
type qualityState struct {
	token             int64
	session, question string
	questionSet       bool
	questionTruncated bool
	steps             []qualityStep
	mu                sync.Mutex
}
type qualityEvent struct {
	TraceID          string        `json:"trace_id"`
	TokenID          int64         `json:"token_id"`
	SessionID        string        `json:"session_id"`
	Category         string        `json:"category"`
	Status           int           `json:"status"`
	LatencyMS        int64         `json:"latency_ms"`
	ErrorCode        string        `json:"error_code"`
	Complete         bool          `json:"complete"`
	Origin           string        `json:"origin"`
	InputTokens      *int64        `json:"input_tokens,omitempty"`
	OutputTokens     *int64        `json:"output_tokens,omitempty"`
	CaptureTruncated bool          `json:"capture_truncated"`
	Question         string        `json:"question"`
	Answer           string        `json:"answer"`
	Steps            []qualityStep `json:"steps"`
}
type qualitySink interface{ Submit(qualityEvent) }
type qualitySender struct {
	endpoint, token            string
	client                     *http.Client
	queue                      chan qualityEvent
	delivered, dropped, failed atomic.Uint64
	lastWarning                atomic.Int64
}

func qualityBound(s string, n int) string {
	if len(s) <= n {
		return s
	}
	s = s[:n]
	for !utf8.ValidString(s) && len(s) > 0 {
		s = s[:len(s)-1]
	}
	return s
}
func qualityBind(r *http.Request, tokenID int64) {
	if s, ok := r.Context().Value(qualityContextKey{}).(*qualityState); ok {
		s.mu.Lock()
		s.token = tokenID
		s.mu.Unlock()
	}
}
func qualityQuestion(r *http.Request, session, question string) {
	if s, ok := r.Context().Value(qualityContextKey{}).(*qualityState); ok {
		s.mu.Lock()
		s.session = qualityBound(session, 256)
		s.question = qualityBound(question, 16384)
		s.questionTruncated = len(question) > 16384
		s.questionSet = true
		s.mu.Unlock()
	}
}
func qualityRecordStep(ctx context.Context, skill string, started time.Time, failed bool) {
	if s, ok := ctx.Value(qualityContextKey{}).(*qualityState); ok {
		state := "completed"
		if failed {
			state = "failed"
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		if len(s.steps) < 128 {
			s.steps = append(s.steps, qualityStep{"tool.server_executed", qualityBound(skill, 128), "server_executor", state, time.Since(started).Milliseconds()})
		}
	}
}
func qualityCategory(path string) string {
	for _, v := range []struct{ needle, category string }{{"/image", "image"}, {"/video", "video"}, {"/vision", "ocr"}, {"/ocr", "ocr"}, {"/audio", "audio"}, {"/coding", "coding"}, {"/search", "search"}, {"/chat", "chat"}} {
		if strings.Contains(path, v.needle) {
			return v.category
		}
	}
	return "other"
}

type qualityCapture struct {
	http.ResponseWriter
	status                                                   int
	jsonBody, line                                           []byte
	data                                                     []string
	dataBytes                                                int
	text                                                     strings.Builder
	steps                                                    []qualityStep
	complete, truncated, failed, writeFailed, payloadInvalid bool
	errorCode                                                string
	requested                                                map[int]string
	inputTokens, outputTokens                                *int64
}

func (w *qualityCapture) Unwrap() http.ResponseWriter { return w.ResponseWriter }
func (w *qualityCapture) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
		w.ResponseWriter.WriteHeader(status)
	}
}
func (w *qualityCapture) Flush() {
	if w.status == 0 {
		w.WriteHeader(200)
	}
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}
func (w *qualityCapture) Write(p []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(200)
	}
	n, err := w.ResponseWriter.Write(p)
	if err != nil {
		w.writeFailed = true
	}
	if strings.Contains(w.Header().Get("Content-Type"), "text/event-stream") {
		for _, b := range p[:n] {
			if b == '\n' {
				line := strings.TrimSuffix(string(w.line), "\r")
				w.line = nil
				if line == "" {
					w.event(strings.Join(w.data, "\n"))
					w.data = nil
					w.dataBytes = 0
				} else if strings.HasPrefix(line, "data:") {
					part := strings.TrimSpace(line[5:])
					w.dataBytes += len(part) + 1
					if w.dataBytes <= 131072 {
						w.data = append(w.data, part)
					} else {
						w.data = nil
						w.truncated = true
					}
				}
			} else if len(w.line) < 131072 {
				w.line = append(w.line, b)
			} else {
				w.truncated = true
			}
		}
	} else if strings.Contains(w.Header().Get("Content-Type"), "json") {
		room := 131072 - len(w.jsonBody)
		if n > room {
			w.truncated = true
		}
		if room > 0 {
			w.jsonBody = append(w.jsonBody, p[:min(n, room)]...)
		}
	}
	return n, err
}
func (w *qualityCapture) addText(text string) {
	room := qualityTextLimit - w.text.Len()
	if len(text) > room {
		w.truncated = true
	}
	if room > 0 {
		w.text.WriteString(qualityBound(text, room))
	}
}
func (w *qualityCapture) event(raw string) {
	if raw == "[DONE]" {
		w.complete = true
		return
	}
	if raw == "" {
		return
	}
	var v map[string]json.RawMessage
	if json.Unmarshal([]byte(raw), &v) != nil {
		w.failed = true
		w.payloadInvalid = true
		return
	}
	if err, ok := v["error"]; ok && string(err) != "null" {
		w.failed = true
		var detail struct {
			Code string `json:"code"`
		}
		_ = json.Unmarshal(err, &detail)
		switch detail.Code {
		case "timeout", "skills_timeout", "operation_timeout":
			w.errorCode = "timeout"
		case "bad_payload", "invalid_worker_result":
			w.errorCode = "bad_payload"
		case "backend_unavailable", "engine_unavailable", "skills_unavailable":
			w.errorCode = "upstream_unavailable"
		}
	}
	if ok, exists := v["ok"]; exists && string(ok) == "false" {
		w.failed = true
	}
	var reported struct {
		JobID  string `json:"job_id"`
		ID     string `json:"id"`
		Status string `json:"status"`
		SHA    string `json:"sha256"`
		Result struct {
			SHA string `json:"sha256"`
		} `json:"result"`
	}
	if json.Unmarshal([]byte(raw), &reported) == nil {
		job := reported.JobID
		if job == "" {
			job = reported.ID
		}
		switch reported.Status {
		case "queued", "running", "completed", "failed", "cancelled", "requires_approval":
			if job != "" && len(job) <= 100 && len(w.steps) < 128 {
				w.steps = append(w.steps, qualityStep{"job.status", "job:" + job, "server_reported", reported.Status, 0})
			}
		}
		digest := reported.SHA
		if digest == "" {
			digest = reported.Result.SHA
		}
		if len(digest) == 64 {
			if _, err := hex.DecodeString(digest); err == nil && len(w.steps) < 128 {
				w.steps = append(w.steps, qualityStep{"artifact.reported", "artifact:" + digest, "server_reported", "completed", 0})
			}
		}
	}
	var typ string
	_ = json.Unmarshal(v["type"], &typ)
	if typ == "error" {
		w.failed = true
	}
	if typ == "final" || typ == "done" {
		w.complete = true
	}
	var choices []struct {
		Delta struct {
			Content   string `json:"content"`
			ToolCalls []struct {
				Index    int `json:"index"`
				Function struct {
					Name string `json:"name"`
				} `json:"function"`
			} `json:"tool_calls"`
		} `json:"delta"`
		Message struct {
			Content   string `json:"content"`
			ToolCalls []struct {
				Function struct {
					Name string `json:"name"`
				} `json:"function"`
			} `json:"tool_calls"`
		} `json:"message"`
	}
	if json.Unmarshal(v["choices"], &choices) == nil && len(choices) > 0 {
		c := choices[0]
		if c.Delta.Content != "" {
			w.addText(c.Delta.Content)
		} else {
			w.addText(c.Message.Content)
		}
		for _, call := range c.Delta.ToolCalls {
			if call.Function.Name != "" && call.Index >= 0 && call.Index < 32 {
				if w.requested == nil {
					w.requested = map[int]string{}
				}
				w.requested[call.Index] = qualityBound(w.requested[call.Index]+call.Function.Name, 128)
			}
		}
		for _, call := range c.Message.ToolCalls {
			if call.Function.Name != "" && len(w.steps) < 128 {
				w.steps = append(w.steps, qualityStep{"tool.requested", qualityBound(call.Function.Name, 128), "model_requested", "requested", 0})
			}
		}
	} else {
		// Visible answer fields only; never reasoning, analysis or arbitrary nested payloads.
		for _, key := range []string{"answer", "text", "content", "result"} {
			var text string
			if json.Unmarshal(v[key], &text) == nil && text != "" {
				if w.text.Len() == 0 || (typ != "final" && typ != "done") {
					w.addText(text)
				}
				break
			}
		}
	}
	var usage struct {
		Prompt     *int64 `json:"prompt_tokens"`
		Completion *int64 `json:"completion_tokens"`
	}
	if json.Unmarshal(v["usage"], &usage) == nil {
		if usage.Prompt != nil {
			w.inputTokens = usage.Prompt
		}
		if usage.Completion != nil {
			w.outputTokens = usage.Completion
		}
	}
}

type qualityBody struct {
	io.ReadCloser
	captured  *bytes.Buffer
	truncated *bool
}

func (r qualityBody) Read(p []byte) (int, error) {
	n, err := r.ReadCloser.Read(p)
	room := 196608 - r.captured.Len()
	if n > room {
		*r.truncated = true
	}
	if room > 0 {
		r.captured.Write(p[:min(n, room)])
	}
	return n, err
}
func qualityMiddleware(sink qualitySink, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/v1/") || strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
			next.ServeHTTP(w, r)
			return
		}
		start := time.Now()
		state := &qualityState{}
		r = r.WithContext(context.WithValue(r.Context(), qualityContextKey{}, state))
		body := bytes.Buffer{}
		inputTruncated := false
		if r.Body != nil && strings.Contains(r.Header.Get("Content-Type"), "json") {
			r.Body = qualityBody{r.Body, &body, &inputTruncated}
		}
		capture := &qualityCapture{ResponseWriter: w}
		var panicValue any
		func() { defer func() { panicValue = recover() }(); next.ServeHTTP(capture, r) }()
		state.mu.Lock()
		defer state.mu.Unlock()
		if state.token < 1 {
			if panicValue != nil {
				panic(panicValue)
			}
			return
		}
		if panicValue != nil {
			capture.failed = true
			if capture.status == 0 {
				capture.status = 500
			}
		}
		if !state.questionSet && body.Len() > 0 {
			var in struct {
				SessionID string `json:"session_id"`
				Prompt    string `json:"prompt"`
				Messages  []struct {
					Role    string          `json:"role"`
					Content json.RawMessage `json:"content"`
				} `json:"messages"`
			}
			if json.Unmarshal(body.Bytes(), &in) == nil {
				state.session = qualityBound(in.SessionID, 256)
				state.question = qualityBound(in.Prompt, 16384)
				for i := len(in.Messages) - 1; i >= 0; i-- {
					if in.Messages[i].Role == "user" {
						var text string
						if json.Unmarshal(in.Messages[i].Content, &text) == nil {
							state.question = qualityBound(text, 16384)
						}
						break
					}
				}
			}
		}
		stream := strings.Contains(capture.Header().Get("Content-Type"), "text/event-stream")
		if !stream && len(capture.jsonBody) > 0 && !capture.truncated {
			capture.event(string(capture.jsonBody))
		}
		status := capture.status
		if status == 0 {
			status = 200
		}
		code := "none"
		if status >= 400 || capture.failed {
			code = "unknown_error"
		}
		if capture.payloadInvalid {
			code = "bad_payload"
		}
		if capture.errorCode != "" {
			code = capture.errorCode
		}
		if status == 400 || status == 413 || status == 422 {
			code = "invalid_input"
		}
		if status == 401 || status == 403 {
			code = "access_denied"
		}
		if status == 502 || status == 503 {
			code = "upstream_unavailable"
		}
		if status == 504 {
			code = "timeout"
		}
		if stream && !capture.complete {
			code = "stream_incomplete"
		}
		if capture.writeFailed || r.Context().Err() != nil {
			code = "cancelled"
		}
		var id [16]byte
		if _, err := rand.Read(id[:]); err != nil {
			if panicValue != nil {
				panic(panicValue)
			}
			return
		}
		steps := append([]qualityStep{}, state.steps...)
		steps = append(steps, capture.steps...)
		for i := 0; i < 32; i++ {
			if name := capture.requested[i]; name != "" {
				steps = append(steps, qualityStep{"tool.requested", name, "model_requested", "requested", 0})
			}
		}
		if len(steps) > 128 {
			capture.truncated = true
			steps = steps[:128]
		}
		sink.Submit(qualityEvent{TraceID: hex.EncodeToString(id[:]), TokenID: state.token, SessionID: state.session,
			Category: qualityCategory(r.URL.Path), Status: status, LatencyMS: time.Since(start).Milliseconds(), ErrorCode: code,
			Complete: status < 400 && !capture.failed && !capture.writeFailed && r.Context().Err() == nil && (!stream || capture.complete), Origin: "gateway",
			InputTokens: capture.inputTokens, OutputTokens: capture.outputTokens, CaptureTruncated: inputTruncated || capture.truncated || state.questionTruncated,
			Question: state.question, Answer: capture.text.String(), Steps: steps})
		if panicValue != nil {
			panic(panicValue)
		}
	})
}
func (s *qualitySender) warning() {
	now := time.Now().Unix()
	last := s.lastWarning.Load()
	if now-last >= 30 && s.lastWarning.CompareAndSwap(last, now) {
		log.Printf("quality_capture_gap delivered=%d dropped=%d failed=%d", s.delivered.Load(), s.dropped.Load(), s.failed.Load())
	}
}
func (s *qualitySender) Submit(v qualityEvent) {
	select {
	case s.queue <- v:
	default:
		s.dropped.Add(1)
		s.warning()
	}
}
func (s *qualitySender) work() {
	for event := range s.queue {
		raw, err := json.Marshal(event)
		if err != nil {
			s.failed.Add(1)
			continue
		}
		req, err := http.NewRequest("POST", s.endpoint, bytes.NewReader(raw))
		if err != nil {
			s.failed.Add(1)
			s.warning()
			continue
		}
		req.Header.Set("Authorization", "Bearer "+s.token)
		req.Header.Set("Content-Type", "application/json")
		response, err := s.client.Do(req)
		if err != nil {
			s.failed.Add(1)
			s.warning()
			continue
		}
		io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		response.Body.Close()
		if response.StatusCode != 200 {
			s.failed.Add(1)
			s.warning()
		} else {
			s.delivered.Add(1)
		}
	}
}
func qualityFromEnvironment(next http.Handler) http.Handler {
	endpoint := os.Getenv("HARMONIKA_QUALITY_AUDIT_URL")
	tokenFile := os.Getenv("HARMONIKA_QUALITY_INGEST_TOKEN_FILE")
	if endpoint == "" {
		return next
	}
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Path != "/ingest" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		log.Print("quality_capture_disabled reason=invalid_endpoint")
		return next
	}
	info, err := os.Stat(tokenFile)
	if err != nil || info.Mode().Perm()&0077 != 0 {
		log.Print("quality_capture_disabled reason=private_token_file_required")
		return next
	}
	raw, err := os.ReadFile(tokenFile)
	if err != nil || len(strings.TrimSpace(string(raw))) < 32 {
		log.Print("quality_capture_disabled reason=invalid_service_token")
		return next
	}
	sender := &qualitySender{endpoint: endpoint, token: strings.TrimSpace(string(raw)), queue: make(chan qualityEvent, 256),
		client: &http.Client{Timeout: 2 * time.Second, Transport: &http.Transport{Proxy: nil, MaxIdleConnsPerHost: 2}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	go sender.work()
	log.Print("quality_capture_enabled queue_capacity=256")
	return qualityMiddleware(sender, next)
}
