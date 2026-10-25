package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"
)

func TestTelcoLive(t *testing.T) {
	if os.Getenv("HARMONIKA_TELCO_LIVE") != "1" {
		t.Skip("explicit isolated opt-in")
	}
	db, e := sql.Open("pgx", os.Getenv("DATABASE_URL"))
	if e != nil {
		t.Fatal("DB unavailable")
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	ids := []int64{}
	keys := []string{}
	defer func() {
		for _, id := range ids {
			db.Exec("DELETE FROM public_ai_requests WHERE token_id=$1", id)
			db.Exec("DELETE FROM public_ai_tokens WHERE id=$1", id)
		}
	}()
	for n := 0; n < 2; n++ {
		raw := make([]byte, 32)
		rand.Read(raw)
		key := "hai_live_" + hex.EncodeToString(raw)
		var id int64
		if db.QueryRowContext(ctx, `INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,request_limit_per_day) VALUES($1,'isolated-telco-smoke','isolated-telco-smoke','chat,models',80) RETURNING id`, hashToken(key)).Scan(&id) != nil {
			t.Fatal("test token creation failed")
		}
		ids = append(ids, id)
		keys = append(keys, key)
		t.Logf("isolated_actor=api-token:%d", id)
	}
	engine, _ := url.Parse("http://127.0.0.1:17890")
	a := &app{db: db, engine: engine}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/telco/", a.publicTelcoAPI)
	mux.HandleFunc("/v1/chat/completions", a.publicAIChatCompletions)
	srv := httptest.NewServer(mux)
	defer srv.Close()
	base := srv.URL
	if os.Getenv("HARMONIKA_TELCO_BASE") != "" {
		base = os.Getenv("HARMONIKA_TELCO_BASE")
	}
	call := func(method, path string, input any, key string, status int, idem string) map[string]any {
		body, _ := json.Marshal(input)
		r, _ := http.NewRequestWithContext(ctx, method, base+path, bytes.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+key)
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("X-Harmonika-Principal", fmt.Sprintf("api-token:%d", ids[1]))
		if idem != "" {
			r.Header.Set("Idempotency-Key", idem)
		}
		res, err := http.DefaultClient.Do(r)
		if err != nil {
			t.Fatal("HTTP failed", err)
		}
		defer res.Body.Close()
		raw, _ := io.ReadAll(io.LimitReader(res.Body, 1048577))
		if res.StatusCode != status {
			t.Fatalf("%s expected%d got%d %.700s", path, status, res.StatusCode, raw)
		}
		v := map[string]any{}
		if json.Unmarshal(raw, &v) != nil {
			t.Fatal("invalid JSON")
		}
		return v
	}
	c := call("GET", "/v1/telco/capabilities", nil, keys[0], 200, "")
	if c["document_skill_ids"] != float64(276) || c["scope"].(map[string]any)["company_data_connected"] != false {
		t.Fatal("catalog or scope invalid")
	}
	call("GET", "/v1/telco/capabilities", nil, "invalid", 401, "")
	j := call("POST", "/v1/telco/invoke", map[string]any{"skill_id": "sub.create", "input": map[string]any{"name": "Isolated Telco Fixture"}}, keys[0], 200, "fixture-create")
	if j["status"] != "approval_required" {
		t.Fatal("mutation bypassed approval")
	}
	path := "/v1/telco/requests/" + j["id"].(string)
	call("GET", path, nil, keys[1], 404, "")
	call("POST", path+"/approve", map[string]any{"decision": "approve", "input_hash": j["input_hash"]}, keys[1], 404, "fixture-bad-actor")
	done := call("POST", path+"/approve", map[string]any{"decision": "approve", "input_hash": j["input_hash"]}, keys[0], 200, "fixture-approve")
	if done["status"] != "completed" {
		t.Fatal("local create failed")
	}
	replay := call("POST", "/v1/telco/invoke", map[string]any{"skill_id": "sub.create", "input": map[string]any{"name": "Isolated Telco Fixture"}}, keys[0], 200, "fixture-create")
	if replay["replayed"] != true || replay["id"] != j["id"] {
		t.Fatal("idempotency lost")
	}
	call("POST", "/v1/telco/invoke", map[string]any{"skill_id": "sub.create", "input": map[string]any{"name": "Different"}}, keys[0], 409, "fixture-create")
	call("POST", "/v1/telco/invoke", map[string]any{"skill_id": "olt.firmware.upgrade", "input": map[string]any{}}, keys[0], 503, "fixture-blocked")
	er, _ := http.NewRequestWithContext(ctx, "GET", base+path+"/events", nil)
	er.Header.Set("Authorization", "Bearer "+keys[0])
	er.Header.Set("Accept", "text/event-stream")
	er.Header.Set("Last-Event-ID", j["id"].(string)+":1")
	es, err := http.DefaultClient.Do(er)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(io.LimitReader(es.Body, 1048576))
	es.Body.Close()
	if es.StatusCode != 200 || !strings.Contains(string(b), "[DONE]") || strings.Contains(string(b), "id: "+j["id"].(string)+":1\n") {
		t.Fatal("SSE replay failed")
	}
	// GET recovery and events must never consume public request quota.
	var before, after int
	db.QueryRow("SELECT count(*) FROM public_ai_requests WHERE token_id=$1", ids[0]).Scan(&before)
	call("GET", path, nil, keys[0], 200, "")
	call("GET", path+"/events?after_sequence=0", nil, keys[0], 200, "")
	db.QueryRow("SELECT count(*) FROM public_ai_requests WHERE token_id=$1", ids[0]).Scan(&after)
	if before != after {
		t.Fatal("recovery double-charged quota")
	}
	// Real mentor chooses a declared read-only tool, without client naming a skill.
	chat := call("POST", "/v1/chat/completions", map[string]any{"model": "harmonika-ai", "telco": true, "messages": []map[string]string{{"role": "user", "content": "Buat laporan ARPU periode 2026-09 dari data tenant ini. Panggil tool laporan ARPU yang tersedia; jangan mengarang data perusahaan."}}}, keys[0], 200, "fixture-agent")
	meta, ok := chat["harmonika"].(map[string]any)
	if !ok || len(meta["steps"].([]any)) == 0 || meta["status"] != "completed" {
		t.Fatalf("actual planner did not complete tool flow: %.1000s", mustJSON(chat))
	}
	if meta["scope"].(map[string]any)["company_data_connected"] != false {
		t.Fatal("false live-source claim")
	}
	mutation := call("POST", "/v1/telco/agent", map[string]any{"messages": []map[string]string{{"role":"user","content":"Buat record pelanggan lokal baru dengan nama Isolated Pending Telco Fixture. Gunakan tool sub.create yang tersedia; siapkan preview saja, jangan menyetujui sendiri. Jangan membuat paket atau data lain."}}, "stream":false}, keys[0], 200, "fixture-agent-mutation")
	if mutation["status"]!="approval_required" {t.Fatalf("mutation planner bypassed preview: %.1000s",mustJSON(mutation))}
	pending:=mutation["request"].(map[string]any);if pending["skill_id"]!="sub.create"{t.Fatal("unexpected mutation")}
	stopped:=call("POST","/v1/telco/requests/"+pending["id"].(string)+"/stop",map[string]any{},keys[0],200,"");if stopped["status"]!="stopped"{t.Fatal("stop failed")}
	t.Log("telco auth/ownership/approval/idempotency/disabled adapter/SSE/quota/actual planner PASS")
}
