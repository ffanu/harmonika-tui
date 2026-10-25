package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"
)

// Opt-in only; model calls use synthetic snippets and an isolated short-lived
// public token. Existing keys and customer conversations are not read or changed.
func TestEngineeringLive(t *testing.T) {
	if os.Getenv("HARMONIKA_ENGINEERING_LIVE") != "1" {
		t.Skip("live opt-in only")
	}
	if len(autoSkillCatalog()) != 16 {
		t.Fatal("engineering runtime not ready; no live calls made")
	}
	db, e := sql.Open("pgx", os.Getenv("DATABASE_URL"))
	if e != nil {
		t.Fatal("DB unavailable")
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 360*time.Second)
	defer cancel()
	entropy := make([]byte, 32)
	rand.Read(entropy)
	key := "hai_live_" + hex.EncodeToString(entropy)
	var id int64
	e = db.QueryRowContext(ctx, `INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,request_limit_per_day) VALUES($1,'isolated-engineering-smoke','isolated-engineering-smoke','chat,models',20) RETURNING id`, hashToken(key)).Scan(&id)
	if e != nil {
		t.Fatal("isolated key setup failed")
	}
	defer func() {
		db.Exec(`DELETE FROM public_ai_requests WHERE token_id=$1`, id)
		db.Exec(`DELETE FROM public_ai_tokens WHERE id=$1`, id)
	}()
	engine, _ := url.Parse("http://127.0.0.1:17890")
	a := &app{db: db, engine: engine}
	srv := httptest.NewServer(http.HandlerFunc(a.publicAIChatCompletions))
	defer srv.Close()
	base := os.Getenv("HARMONIKA_ENGINEERING_BASE")
	if base == "" {
		base = srv.URL
	}
	cases := []struct{ name, prompt, operation string }{
		{"code_review", "Review keamanan kode Python ini. Beri temuan dengan baris yang sesuai:\n```python\nimport subprocess\ndef run_user(command):\n    return subprocess.check_output(command, shell=True)\n```", "engineering.code_review"},
		{"debug", "Debug error ini berdasarkan bukti, jangan klaim sudah diperbaiki:\n```log\nTraceback (most recent call last):\n  File app.py, line 3, in get_name\nKeyError: 'name'\n```\n```python\ndef get_name(data):\n    return data['name']\n```", "engineering.debug"},
		{"testing_strategy", "Buatkan test plan endpoint pembayaran yang harus menolak nominal negatif, memastikan idempotensi dan memvalidasi kepemilikan invoice. Jangan jalankan test.", "engineering.testing_strategy"},
		{"documentation", "Buat README dari kode berikut; jangan mengarang port atau cara deployment:\n```python\ndef add(a, b):\n    return a + b\n```", "engineering.documentation"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			body, _ := json.Marshal(map[string]any{"model": "harmonika-ai", "stream": c.name == "documentation", "messages": []map[string]any{{"role": "user", "content": c.prompt}}})
			req, _ := http.NewRequestWithContext(ctx, "POST", base+"/v1/chat/completions", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer "+key)
			began := time.Now()
			res, e := http.DefaultClient.Do(req)
			if e != nil {
				t.Fatal("HTTP unavailable")
			}
			defer res.Body.Close()
			raw, _ := io.ReadAll(io.LimitReader(res.Body, 1<<20))
			var out struct {
				Harmonika struct {
					N       int `json:"tool_executions"`
					Reports []struct {
						Operation string `json:"operation"`
						Status    string `json:"status"`
						Method    struct {
							Executed bool `json:"code_executed"`
							Fetched  bool `json:"repository_fetched"`
						} `json:"method"`
					} `json:"reports"`
				} `json:"harmonika"`
				Choices []struct {
					Message struct {
						Content string `json:"content"`
					} `json:"message"`
				} `json:"choices"`
			}
			if c.name == "documentation" && res.StatusCode == 200 {
				content := ""
				var reports any
				done := false
				terminal := false
				for _, line := range strings.Split(string(raw), "\n") {
					if !strings.HasPrefix(line, "data: ") {
						continue
					}
					data := strings.TrimPrefix(line, "data: ")
					if data == "[DONE]" {
						done = true
						continue
					}
					var chunk map[string]any
					if json.Unmarshal([]byte(data), &chunk) != nil {
						continue
					}
					if h, ok := chunk["harmonika"].(map[string]any); ok {
						reports = h["reports"]
					}
					if choices, ok := chunk["choices"].([]any); ok {
						for _, v := range choices {
							choice, _ := v.(map[string]any)
							if choice["finish_reason"] == "stop" {
								terminal = true
							}
							delta, _ := choice["delta"].(map[string]any)
							if text, ok := delta["content"].(string); ok {
								content += text
							}
						}
					}
				}
				if !done || !terminal {
					t.Fatal("incomplete engineering SSE")
				}
				items, _ := reports.([]any)
				raw, _ = json.Marshal(map[string]any{"harmonika": map[string]any{"reports": reports, "tool_executions": len(items)}, "choices": []any{map[string]any{"message": map[string]any{"content": content}}}})
				t.Log("engineering SSE terminal reports and DONE verified")
			}
			if res.StatusCode != 200 || json.Unmarshal(raw, &out) != nil || len(out.Choices) != 1 || out.Choices[0].Message.Content == "" {
				t.Fatalf("HTTP %d response redacted", res.StatusCode)
			}
			found := false
			for _, r := range out.Harmonika.Reports {
				if r.Operation == c.operation {
					found = true
					if r.Method.Executed || r.Method.Fetched {
						t.Fatal("false execution claim")
					}
				}
			}
			if !found || out.Harmonika.N < 1 {
				t.Fatal("requested workflow not actually executed")
			}
			t.Logf("HTTP200 operation=%s tools=%d seconds=%.2f", c.operation, out.Harmonika.N, time.Since(began).Seconds())
			t.Logf("answer excerpt: %.240s", strings.ReplaceAll(out.Choices[0].Message.Content, "\n", " "))
		})
	}
	if base != srv.URL {
		t.Run("capabilities", func(t *testing.T) {
			req, _ := http.NewRequestWithContext(ctx, "GET", base+"/v1/public-ai/capabilities", nil)
			req.Header.Set("Authorization", "Bearer "+key)
			res, e := http.DefaultClient.Do(req)
			if e != nil {
				t.Fatal("capability HTTP unavailable")
			}
			defer res.Body.Close()
			var caps struct {
				Features map[string]any `json:"features"`
				Limits   map[string]any `json:"limits"`
			}
			if json.NewDecoder(io.LimitReader(res.Body, 1<<20)).Decode(&caps) != nil || res.StatusCode != 200 {
				t.Fatal("invalid capabilities")
			}
			if caps.Features["automatic_engineering"] != true || caps.Features["engineering_source_input"] != true || caps.Features["engineering_repository_connector"] != false || caps.Features["engineering_code_execution"] != false {
				t.Fatal("capability readiness mismatch")
			}
			if caps.Limits["automatic_context_max_bytes"] != float64(245760) {
				t.Fatal("context limit mismatch")
			}
			t.Log("automatic_engineering=true, source_input=true, private_repository=false, code_execution=false; 240KiB automatic context")
		})
	}

}
