package main

// Tenant-local telecom domain core; API keys never grant live network/billing access.
import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"
)

var telcoPath = regexp.MustCompile(`^/v1/telco/(capabilities|invoke|agent|requests/[0-9a-f-]{36}(/(events|approve|stop))?)$`)

func publicTelcoReady() bool {
	if _, e := os.Stat("/var/lib/harmonika-telco/ready.json"); e != nil {
		return false
	}
	client := http.Client{Timeout: 300 * time.Millisecond}
	r, e := client.Get("http://127.0.0.1:18189/healthz")
	if e != nil {
		return false
	}
	r.Body.Close()
	return r.StatusCode == 200
}
func telcoScopeAllowed(t publicAIToken) bool {
	for _, s := range strings.Split(t.Scopes, ",") {
		if s == "chat" || s == "telco" {
			return true
		}
	}
	return false
}
func telcoUpstream(ctx context.Context, tok publicAIToken, method, path string, body []byte, headers http.Header) (*http.Response, error) {
	secret, e := os.ReadFile("/etc/harmonika-ai/telco-engine.token")
	if e != nil {
		return nil, fmt.Errorf("telco_unavailable")
	}
	req, e := http.NewRequestWithContext(ctx, method, "http://127.0.0.1:18189"+path, bytes.NewReader(body))
	if e != nil {
		return nil, e
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Harmonika-Telco-Secret", strings.TrimSpace(string(secret)))
	req.Header.Set("X-Harmonika-Principal", fmt.Sprintf("api-token:%d", tok.ID))
	for _, name := range []string{"Accept", "Idempotency-Key", "Last-Event-ID"} {
		if x := headers.Get(name); x != "" {
			req.Header.Set(name, x)
		}
	}
	client := http.Client{CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	return client.Do(req)
}
func (a *app) telcoToken(w http.ResponseWriter, r *http.Request) (publicAIToken, bool) {
	var tok publicAIToken
	key := bearerToken(r)
	if key == "" {
		jsonOut(w, 401, map[string]any{"error": map[string]any{"code": "missing_api_key"}})
		return tok, false
	}
	e := a.db.QueryRowContext(r.Context(), "SELECT id,name,owner_chat_id,scopes,tier,request_limit_per_day,unlimited FROM public_ai_tokens WHERE token_hash=$1 AND status='active' AND revoked_at IS NULL", hashToken(key)).Scan(&tok.ID, &tok.Name, &tok.OwnerID, &tok.Scopes, &tok.Tier, &tok.RequestLimit, &tok.Unlimited)
	if e != nil {
		status, code := 503, "authentication_unavailable"
		if errors.Is(e, sql.ErrNoRows) {
			status, code = 401, "invalid_api_key"
		}
		jsonOut(w, status, map[string]any{"error": map[string]any{"code": code}})
		return tok, false
	}
	return tok, true
}
func (a *app) publicTelcoAPI(w http.ResponseWriter, r *http.Request) {
	if !telcoPath.MatchString(r.URL.Path) {
		jsonOut(w, 404, map[string]any{"error": map[string]any{"code": "not_found"}})
		return
	}
	tok, ok := a.telcoToken(w, r)
	if !ok {
		return
	}
	if !telcoScopeAllowed(tok) {
		jsonOut(w, 403, map[string]any{"error": map[string]any{"code": "telco_scope_required"}})
		return
	}
	if r.Method != "GET" && r.Method != "POST" {
		jsonOut(w, 405, map[string]any{"error": map[string]any{"code": "method_not_allowed"}})
		return
	}
	if r.Method == "POST" && !strings.HasSuffix(r.URL.Path, "/stop") {
		if status, code := a.reservePublicAIQuota(r.Context(), &tok, r); status != 0 {
			jsonOut(w, status, map[string]any{"error": map[string]any{"code": code}})
			return
		}
	}
	body, e := io.ReadAll(http.MaxBytesReader(w, r.Body, 262144))
	if e != nil {
		jsonOut(w, 413, map[string]any{"error": map[string]any{"code": "input_too_large"}})
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/v1/telco")
	if r.URL.RawQuery != "" {
		v := r.URL.Query()
		if len(v) != 1 || v.Get("after_sequence") == "" {
			jsonOut(w, 400, map[string]any{"error": map[string]any{"code": "unsupported_query"}})
			return
		}
		path += "?" + v.Encode()
	}
	ctx, cancel := context.WithTimeout(r.Context(), 190*time.Second)
	defer cancel()
	res, e := telcoUpstream(ctx, tok, r.Method, path, body, r.Header)
	if e != nil {
		jsonOut(w, 503, map[string]any{"error": map[string]any{"code": "telco_unavailable"}})
		return
	}
	defer res.Body.Close()
	forwardTelcoResponse(w, res)
}
func forwardTelcoResponse(w http.ResponseWriter, res *http.Response) {
	w.Header().Set("Cache-Control", "no-store")
	if strings.HasPrefix(res.Header.Get("Content-Type"), "text/event-stream") {
		w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
		w.Header().Set("X-Accel-Buffering", "no")
		w.WriteHeader(res.StatusCode)
		f, _ := w.(http.Flusher)
		buf := make([]byte, 8192)
		total := 0
		for {
			n, e := res.Body.Read(buf)
			total += n
			if total > 4*1024*1024 {
				return
			}
			if n > 0 {
				if _, e := w.Write(buf[:n]); e != nil {
					return
				}
				if f != nil {
					f.Flush()
				}
			}
			if e != nil {
				return
			}
		}
	}
	raw, e := io.ReadAll(io.LimitReader(res.Body, 1048577))
	if e != nil || len(raw) > 1048576 || !json.Valid(raw) {
		jsonOut(w, 502, map[string]any{"error": map[string]any{"code": "invalid_telco_response"}})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(res.StatusCode)
	w.Write(raw)
}
func (a *app) publicTelcoChat(w http.ResponseWriter, r *http.Request, tok publicAIToken, in publicAIChatCompletionRequest) {
	if !telcoScopeAllowed(tok) {
		jsonOut(w, 403, map[string]any{"error": map[string]any{"code": "telco_scope_required"}})
		return
	}
	if in.WorkspaceID != "" || len(in.Tools) > 0 || publicAIHasToolHistory(in) || in.ToolChoice != nil {
		jsonOut(w, 400, map[string]any{"error": map[string]any{"code": "conflicting_engine_context"}})
		return
	}
	if !regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`).MatchString(r.Header.Get("Idempotency-Key")) {
		jsonOut(w, 400, map[string]any{"error": map[string]any{"code": "idempotency_required"}})
		return
	}
	messages := []map[string]any{}
	for _, m := range in.Messages {
		messages = append(messages, map[string]any{"role": m.Role, "content": publicAIContentText(m.Content)})
	}
	body, _ := json.Marshal(map[string]any{"messages": messages, "stream": in.Stream})
	if len(body) > 196608 {
		jsonOut(w, 413, map[string]any{"error": map[string]any{"code": "context_too_large"}})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 100*time.Second)
	defer cancel()
	res, e := telcoUpstream(ctx, tok, "POST", "/agent", body, r.Header)
	if e != nil {
		jsonOut(w, 503, map[string]any{"error": map[string]any{"code": "telco_unavailable"}})
		return
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		forwardTelcoResponse(w, res)
		return
	}
	id := "chatcmpl_" + hashToken(fmt.Sprintf("telco-%d-%d", tok.ID, time.Now().UnixNano()))[:24]
	created := time.Now().Unix()
	wrap := func(text string, meta any) map[string]any {
		return map[string]any{"id": id, "object": "chat.completion", "created": created, "model": "harmonika-ai", "choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": text}, "finish_reason": "stop"}}, "harmonika": meta}
	}
	if !in.Stream {
		raw, _ := io.ReadAll(io.LimitReader(res.Body, 1048577))
		var v map[string]any
		if len(raw) > 1048576 || json.Unmarshal(raw, &v) != nil {
			jsonOut(w, 502, map[string]any{"error": map[string]any{"code": "invalid_telco_response"}})
			return
		}
		text, _ := v["text"].(string)
		jsonOut(w, 200, wrap(text, v))
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	f, _ := w.(http.Flusher)
	send := func(delta map[string]any, reason any, meta any) {
		v := map[string]any{"id": id, "object": "chat.completion.chunk", "created": created, "model": "harmonika-ai", "choices": []any{map[string]any{"index": 0, "delta": delta, "finish_reason": reason}}}
		if meta != nil {
			v["harmonika"] = meta
		}
		b, _ := json.Marshal(v)
		fmt.Fprintf(w, "data: %s\n\n", b)
		if f != nil {
			f.Flush()
		}
	}
	send(map[string]any{"role": "assistant"}, nil, nil)
	scan := bufio.NewScanner(res.Body)
	scan.Buffer(make([]byte, 8192), 1048576)
	completed := false
	total := 0
	for scan.Scan() {
		line := scan.Text()
		total += len(line)
		if total > 4*1024*1024 {
			break
		}
		if strings.HasPrefix(line, ":") {
			fmt.Fprint(w, ": harmonika working\n\n")
			if f != nil {
				f.Flush()
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
		if event["type"] == "response.completed" {
			v, _ := event["data"].(map[string]any)
			text, _ := v["text"].(string)
			send(map[string]any{"content": text}, "stop", v)
			completed = true
		} else {
			send(map[string]any{}, nil, event)
		}
	}
	if !completed {
		send(map[string]any{}, "stop", map[string]any{"status": "interrupted", "error": map[string]any{"code": "telco_stream_interrupted"}})
	}
	fmt.Fprint(w, "data: [DONE]\n\n")
	if f != nil {
		f.Flush()
	}
}
