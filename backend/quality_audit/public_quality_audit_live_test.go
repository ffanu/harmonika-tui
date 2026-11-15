package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// Explicit opt-in; the principal is synthetic and its rows are purged afterwards.
func TestQualityLiveCollector(t *testing.T) {
	path := os.Getenv("HARMONIKA_QUALITY_AUDIT_LIVE_CONFIG")
	if path == "" {
		t.Skip("live collector not selected")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal("config unavailable")
	}
	var cfg map[string]any
	if json.Unmarshal(raw, &cfg) != nil {
		t.Fatal("invalid config")
	}
	ingest, err := os.ReadFile(cfg["ingest_token_file"].(string))
	if err != nil {
		t.Fatal("ingest token unavailable")
	}
	admin, err := os.ReadFile(cfg["admin_token_file"].(string))
	if err != nil {
		t.Fatal("admin token unavailable")
	}
	client := &http.Client{Timeout: 4 * time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	const principal int64 = 9223372036854775806
	request := func(method, path string, value any) map[string]any {
		var body io.Reader
		if value != nil {
			b, _ := json.Marshal(value)
			body = bytes.NewReader(b)
		}
		req, _ := http.NewRequest(method, "http://127.0.0.1:18208"+path, body)
		req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(admin)))
		req.Header.Set("Content-Type", "application/json")
		resp, e := client.Do(req)
		if e != nil {
			t.Fatal("collector request failed")
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			t.Fatalf("collector rejected synthetic request: %d", resp.StatusCode)
		}
		var out map[string]any
		if json.NewDecoder(resp.Body).Decode(&out) != nil {
			t.Fatal("invalid response")
		}
		return out
	}
	request("POST", "/consent", map[string]any{"token_id": principal, "granted": true})
	defer func() {
		request("POST", "/purge", map[string]any{"token_id": principal})
		request("POST", "/consent", map[string]any{"token_id": principal, "granted": false})
	}()
	sender := &qualitySender{endpoint: "http://127.0.0.1:18208/ingest", token: strings.TrimSpace(string(ingest)), client: client, queue: make(chan qualityEvent, 4)}
	go sender.work()
	defer close(sender.queue)
	canary := "hai_live_" + strings.Repeat("SYNTHETIC", 6)
	h := qualityMiddleware(sender, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		qualityBind(r, principal)
		qualityQuestion(r, "synthetic-quality-smoke", "Pertanyaan sintetis. api_key="+canary)
		qualityRecordStep(r.Context(), "web.search", time.Now(), false)
		w.Header().Set("Content-Type", "text/event-stream")
		w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"Hasil uji sintetis selesai.\"}}]}\n\ndata: [DONE]\n\n"))
	}))
	h.ServeHTTP(httptest.NewRecorder(), qualityTestRequest())
	for i := 0; i < 40; i++ {
		value := request("GET", "/traces?token_id=9223372036854775806&include_content=true", nil)
		traces := value["traces"].([]any)
		if len(traces) > 0 {
			encoded, _ := json.Marshal(traces)
			if bytes.Contains(encoded, []byte(canary)) || !bytes.Contains(encoded, []byte("REDACTED")) || !bytes.Contains(encoded, []byte("Hasil uji sintetis selesai.")) {
				t.Fatal("live encrypted/redacted roundtrip failed")
			}
			if traces[0].(map[string]any)["complete"].(float64) != 1 {
				t.Fatal("complete state missing")
			}
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatal("synthetic event not delivered")
}
