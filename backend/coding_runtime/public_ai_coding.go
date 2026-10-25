package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
)

var codingPath = regexp.MustCompile("^/v1/coding/(capabilities|workspaces|workspaces/[0-9a-f-]{36}(/(invoke|agent))?|jobs/[0-9a-f-]{36}(/(events|approve|stop))?|debug/sessions/[0-9a-f-]{36}(/(events|actions|stop))?)$")

func codingServiceBase() string {
	configured := strings.TrimSpace(os.Getenv("HARMONIKA_CODING_UPSTREAM"))
	if configured == "http://127.0.0.1:18188" {
		return configured
	}
	return "http://127.0.0.1:18187"
}
func publicCodingReady() bool {
	if _, e := os.Stat("/etc/harmonika-ai/coding-runtime.token"); e != nil {
		return false
	}
	if _, e := os.Stat("/var/lib/harmonika-coding/ready.json"); e != nil {
		return false
	}
	client := http.Client{Timeout: 300 * time.Millisecond}
	res, e := client.Get(codingServiceBase() + "/healthz")
	if e != nil {
		return false
	}
	res.Body.Close()
	return res.StatusCode == 200
}

var codingPhase2Probe struct {
	sync.Mutex
	until time.Time
	ready bool
}

func publicCodingPhase2Ready() bool {
	raw, e := os.ReadFile("/var/lib/harmonika-coding/ready.json")
	if e != nil || len(raw) > 8192 {
		return false
	}
	var marker struct {
		Phase2 bool `json:"phase2"`
	}
	if json.Unmarshal(raw, &marker) != nil || !marker.Phase2 {
		return false
	}
	codingPhase2Probe.Lock()
	defer codingPhase2Probe.Unlock()
	if time.Now().Before(codingPhase2Probe.until) {
		return codingPhase2Probe.ready
	}
	codingPhase2Probe.ready = false
	codingPhase2Probe.until = time.Now().Add(10 * time.Second)
	secret, e := os.ReadFile("/etc/harmonika-ai/coding-runtime.token")
	if e != nil {
		return false
	}
	req, e := http.NewRequest("GET", codingServiceBase()+"/capabilities", nil)
	if e != nil {
		return false
	}
	req.Header.Set("X-Harmonika-Coding-Secret", strings.TrimSpace(string(secret)))
	req.Header.Set("X-Harmonika-Principal", "api-token:999999999999999999")
	client := http.Client{Timeout: 2 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	res, e := client.Do(req)
	if e != nil {
		return false
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return false
	}
	var caps struct {
		Features struct {
			Phase2 bool `json:"phase2"`
			Debug  bool `json:"debug_sessions"`
		} `json:"features"`
	}
	if json.NewDecoder(io.LimitReader(res.Body, 1048576)).Decode(&caps) != nil {
		return false
	}
	codingPhase2Probe.ready = caps.Features.Phase2 && caps.Features.Debug
	return codingPhase2Probe.ready
}
func codingScopeAllowed(tok publicAIToken) bool {
	for _, scope := range strings.Split(tok.Scopes, ",") {
		if strings.TrimSpace(scope) == "chat" || strings.TrimSpace(scope) == "coding" {
			return true
		}
	}
	return false
}
func (a *app) codingToken(w http.ResponseWriter, r *http.Request) (publicAIToken, bool) {
	var tok publicAIToken
	key := bearerToken(r)
	if key == "" {
		jsonOut(w, 401, map[string]any{"error": map[string]any{"code": "missing_api_key"}})
		return tok, false
	}
	e := a.db.QueryRowContext(r.Context(), "SELECT id,name,owner_chat_id,scopes,tier,request_limit_per_day,unlimited FROM public_ai_tokens WHERE token_hash=$1 AND status='active' AND revoked_at IS NULL", hashToken(key)).Scan(&tok.ID, &tok.Name, &tok.OwnerID, &tok.Scopes, &tok.Tier, &tok.RequestLimit, &tok.Unlimited)
	if e != nil {
		jsonOut(w, 401, map[string]any{"error": map[string]any{"code": "invalid_api_key"}})
		return tok, false
	}
	if !codingScopeAllowed(tok) {
		jsonOut(w, 403, map[string]any{"error": map[string]any{"code": "coding_scope_required"}})
		return tok, false
	}
	if r.Method != "GET" && !strings.HasSuffix(r.URL.Path, "/stop") {
		if status, code := a.reservePublicAIQuota(r.Context(), &tok, r); status != 0 {
			jsonOut(w, status, map[string]any{"error": map[string]any{"code": code}})
			return tok, false
		}
	}
	return tok, true
}
func codingUpstream(ctx context.Context, tok publicAIToken, method, path string, body []byte, headers http.Header) (*http.Response, error) {
	secret, e := os.ReadFile("/etc/harmonika-ai/coding-runtime.token")
	if e != nil {
		return nil, fmt.Errorf("coding_unavailable")
	}
	req, e := http.NewRequestWithContext(ctx, method, codingServiceBase()+path, bytes.NewReader(body))
	if e != nil {
		return nil, fmt.Errorf("coding_unavailable")
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Harmonika-Coding-Secret", strings.TrimSpace(string(secret)))
	req.Header.Set("X-Harmonika-Principal", fmt.Sprintf("api-token:%d", tok.ID))
	for _, name := range []string{"Accept", "Last-Event-ID", "Idempotency-Key"} {
		if value := headers.Get(name); value != "" {
			req.Header.Set(name, value)
		}
	}
	return http.DefaultClient.Do(req)
}
func (a *app) publicCodingAPI(w http.ResponseWriter, r *http.Request) {
	if !codingPath.MatchString(r.URL.Path) {
		jsonOut(w, 404, map[string]any{"error": map[string]any{"code": "not_found"}})
		return
	}
	tok, ok := a.codingToken(w, r)
	if !ok {
		return
	}
	if r.Method != "GET" && r.Method != "POST" {
		jsonOut(w, 405, map[string]any{"error": map[string]any{"code": "method_not_allowed"}})
		return
	}
	body, e := io.ReadAll(http.MaxBytesReader(w, r.Body, 1024*1024))
	if e != nil {
		jsonOut(w, 413, map[string]any{"error": map[string]any{"code": "request_too_large"}})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 190*time.Second)
	defer cancel()
	path := strings.TrimPrefix(r.URL.Path, "/v1/coding")
	if r.URL.RawQuery != "" {
		values := r.URL.Query()
		if len(values) != 1 || values.Get("after_sequence") == "" {
			jsonOut(w, 400, map[string]any{"error": map[string]any{"code": "unsupported_query"}})
			return
		}
		path += "?" + values.Encode()
	}
	res, e := codingUpstream(ctx, tok, r.Method, path, body, r.Header)
	if e != nil {
		jsonOut(w, 503, map[string]any{"error": map[string]any{"code": "coding_unavailable"}})
		return
	}
	defer res.Body.Close()
	w.Header().Set("Cache-Control", "no-store")
	if strings.HasPrefix(res.Header.Get("Content-Type"), "text/event-stream") {
		w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
		w.Header().Set("X-Accel-Buffering", "no")
		w.WriteHeader(res.StatusCode)
		flush, _ := w.(http.Flusher)
		b := make([]byte, 8192)
		total := 0
		for {
			n, err := res.Body.Read(b)
			total += n
			if total > 4*1024*1024 {
				return
			}
			if n > 0 {
				if _, e := w.Write(b[:n]); e != nil {
					return
				}
				if flush != nil {
					flush.Flush()
				}
			}
			if err != nil {
				return
			}
		}
	}
	raw, e := io.ReadAll(io.LimitReader(res.Body, 4*1024*1024+1))
	if e != nil || len(raw) > 4*1024*1024 || !json.Valid(raw) {
		jsonOut(w, 502, map[string]any{"error": map[string]any{"code": "invalid_coding_response"}})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(res.StatusCode)
	w.Write(raw)
}
func (a *app) publicCodingChat(w http.ResponseWriter, r *http.Request, tok publicAIToken, in publicAIChatCompletionRequest) {
	if !codingScopeAllowed(tok) {
		jsonOut(w, 403, map[string]any{"error": map[string]any{"code": "coding_scope_required"}})
		return
	}
	if !regexp.MustCompile("^[0-9a-f-]{36}$").MatchString(in.WorkspaceID) {
		jsonOut(w, 400, map[string]any{"error": map[string]any{"code": "invalid_workspace_id"}})
		return
	}
	if !regexp.MustCompile("^[A-Za-z0-9_-]{1,128}$").MatchString(r.Header.Get("Idempotency-Key")) {
		jsonOut(w, 400, map[string]any{"error": map[string]any{"code": "idempotency_required", "message": "Workspace chat requires Idempotency-Key for retry safety."}})
		return
	}
	messages := []map[string]any{}
	for _, m := range in.Messages {
		messages = append(messages, map[string]any{"role": m.Role, "content": publicAIContentText(m.Content)})
	}
	body, _ := json.Marshal(map[string]any{"messages": messages, "stream": in.Stream})
	if len(body) > 240*1024 {
		jsonOut(w, 413, map[string]any{"error": map[string]any{"code": "coding_context_too_large"}})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 190*time.Second)
	defer cancel()
	res, e := codingUpstream(ctx, tok, "POST", "/workspaces/"+in.WorkspaceID+"/agent", body, r.Header)
	if e != nil {
		jsonOut(w, 503, map[string]any{"error": map[string]any{"code": "coding_unavailable"}})
		return
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		raw, _ := io.ReadAll(io.LimitReader(res.Body, 65536))
		if !json.Valid(raw) {
			jsonOut(w, 502, map[string]any{"error": map[string]any{"code": "invalid_coding_response"}})
		} else {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(res.StatusCode)
			w.Write(raw)
		}
		return
	}
	id := "chatcmpl_" + hashToken(fmt.Sprintf("coding-%d-%d", tok.ID, time.Now().UnixNano()))[:24]
	created := time.Now().Unix()
	if !in.Stream {
		raw, _ := io.ReadAll(io.LimitReader(res.Body, 4*1024*1024+1))
		var value map[string]any
		if len(raw) > 4*1024*1024 || json.Unmarshal(raw, &value) != nil {
			jsonOut(w, 502, map[string]any{"error": map[string]any{"code": "invalid_coding_response"}})
			return
		}
		text, _ := value["text"].(string)
		jsonOut(w, 200, map[string]any{"id": id, "object": "chat.completion", "created": created, "model": "harmonika-ai", "choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": text}, "finish_reason": "stop"}}, "harmonika": value})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	flush, _ := w.(http.Flusher)
	send := func(delta map[string]any, reason any, meta any) {
		v := map[string]any{"id": id, "object": "chat.completion.chunk", "created": created, "model": "harmonika-ai", "choices": []any{map[string]any{"index": 0, "delta": delta, "finish_reason": reason}}}
		if meta != nil {
			v["harmonika"] = meta
		}
		b, _ := json.Marshal(v)
		fmt.Fprintf(w, "data: %s\n\n", b)
		if flush != nil {
			flush.Flush()
		}
	}
	send(map[string]any{"role": "assistant"}, nil, nil)
	scanner := bufio.NewScanner(res.Body)
	scanner.Buffer(make([]byte, 8192), 4*1024*1024)
	finished := false
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, ":") {
			fmt.Fprint(w, ": harmonika working\n\n")
			if flush != nil {
				flush.Flush()
			}
			continue
		}
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		data := strings.TrimPrefix(line, "data: ")
		if data == "[DONE]" {
			break
		}
		var event map[string]any
		if json.Unmarshal([]byte(data), &event) != nil {
			continue
		}
		kind, _ := event["type"].(string)
		details, _ := event["data"].(map[string]any)
		if kind == "response.completed" {
			text, _ := details["text"].(string)
			send(map[string]any{"content": text}, nil, event)
			send(map[string]any{}, "stop", event)
			finished = true
		} else if kind == "error" {
			b, _ := json.Marshal(map[string]any{"error": details})
			fmt.Fprintf(w, "data: %s\n\n", b)
		} else {
			send(map[string]any{}, nil, event)
		}
	}
	if !finished {
		fmt.Fprint(w, "data: {\"error\":{\"code\":\"coding_stream_incomplete\"}}\n\n")
	}
	fmt.Fprint(w, "data: [DONE]\n\n")
}
