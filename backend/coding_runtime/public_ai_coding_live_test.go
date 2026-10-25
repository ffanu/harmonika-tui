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
 "os/exec"
	"strings"
	"testing"
	"time"
)

func TestCodingLive(t *testing.T) {
	if os.Getenv("HARMONIKA_CODING_LIVE") != "1" {
		t.Skip("isolated live opt-in")
	}
	db, e := sql.Open("pgx", os.Getenv("DATABASE_URL"))
	if e != nil {
		t.Fatal("DB unavailable")
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 480*time.Second)
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
		if db.QueryRowContext(ctx, `INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,request_limit_per_day) VALUES($1,'isolated-coding-smoke','isolated-coding-smoke','chat,models',80) RETURNING id`, hashToken(key)).Scan(&id) != nil {
			t.Fatal("test token creation failed")
		}
		t.Logf("isolated_actor=api-token:%d", id)
		ids = append(ids, id)
		keys = append(keys, key)
	}
	engine, _ := url.Parse("http://127.0.0.1:17890")
	a := &app{db: db, engine: engine}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/coding/", a.publicCodingAPI)
	mux.HandleFunc("/v1/chat/completions", a.publicAIChatCompletions)
	mux.HandleFunc("/v1/public-ai/capabilities", a.publicAICapabilities)
	srv := httptest.NewServer(mux)
	defer srv.Close()
	base := srv.URL
	if os.Getenv("HARMONIKA_CODING_BASE") != "" {
		base = os.Getenv("HARMONIKA_CODING_BASE")
	}
	call := func(method, path string, input any, key string, status int, idem string) map[string]any {
		body, _ := json.Marshal(input)
		req, _ := http.NewRequestWithContext(ctx, method, base+path, bytes.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+key)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Harmonika-Principal", fmt.Sprintf("api-token:%d", ids[1]))
		if idem != "" {
			req.Header.Set("Idempotency-Key", idem)
		}
		res, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal("HTTP request failed", err)
		}
		defer res.Body.Close()
		raw, _ := io.ReadAll(io.LimitReader(res.Body, 4*1024*1024))
		if res.StatusCode != status {
			t.Fatalf("%s expected%d got%d body%.1000s", path, status, res.StatusCode, raw)
		}
		var value map[string]any
		if json.Unmarshal(raw, &value) != nil {
			t.Fatal("invalid JSON")
		}
		return value
	}
	c := call("GET", "/v1/coding/capabilities", nil, keys[0], 200, "")
	if len(c["skills"].([]any)) != 18 && len(c["skills"].([]any)) != 38 {
		t.Fatal("unexpected coding skill count")
	}
	w := call("POST", "/v1/coding/workspaces", map[string]any{"name": "Isolated coding live", "permissions": map[string]bool{"write": true, "execute": true, "git_commit": true}}, keys[0], 201, "")["workspace"].(map[string]any)
	wid := w["id"].(string)
	call("GET", "/v1/coding/workspaces/"+wid, nil, keys[1], 404, "")
	// Real planner chooses tools from an ordinary coding instruction, no skill_id.
	prompt := map[string]any{"model": "harmonika-ai", "workspace_id": wid, "stream": false, "messages": []map[string]string{{"role": "user", "content": "Buat file hello.py di workspace dengan isi persis print(42) lalu newline. Gunakan tool tulis yang tersedia. Jangan jalankan atau buat file lain."}}}
	result := call("POST", "/v1/chat/completions", prompt, keys[0], 200, "live-write-turn")
	meta, ok := result["harmonika"].(map[string]any)
	if !ok || len(meta["steps"].([]any)) == 0 {
		t.Fatal("planner did not execute a real workspace tool")
	}
	read := call("POST", "/v1/coding/workspaces/"+wid+"/invoke", map[string]any{"skill_id": "fs.read", "input": map[string]any{"path": "hello.py"}}, keys[0], 200, "")
	if !strings.Contains(read["result"].(map[string]any)["content"].(string), "print(42)") {
		t.Fatal("file was not actually written")
	}
	run := map[string]any{"model": "harmonika-ai", "workspace_id": wid, "stream": false, "messages": []map[string]string{{"role": "user", "content": "Gunakan exec_python untuk menjalankan file hello.py yang sudah ada. Siapkan job; jangan mengarang hasil dan jangan mencoba menyetujui sendiri."}}}
	result = call("POST", "/v1/chat/completions", run, keys[0], 200, "live-run-turn")
	meta = result["harmonika"].(map[string]any)
	if meta["status"] != "approval_required" {
		t.Fatalf("execution bypassed approval or planner failed: %.1500s", mustJSON(meta))
	}
	j := meta["job"].(map[string]any)
	jid := j["id"].(string)
	call("POST", "/v1/coding/jobs/"+jid+"/approve", map[string]string{"decision": "approve", "input_hash": j["input_hash"].(string)}, keys[1], 404, "")
	call("POST", "/v1/coding/jobs/"+jid+"/approve", map[string]string{"decision": "approve", "input_hash": j["input_hash"].(string)}, keys[0], 200, "")
	for n := 0; n < 200; n++ {
		j = call("GET", "/v1/coding/jobs/"+jid, nil, keys[0], 200, "")
		if j["status"] != "queued" && j["status"] != "running" {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if j["status"] != "completed" || !strings.Contains(j["result"].(map[string]any)["stdout"].(string), "42") {
		t.Fatal("approved Python did not really run")
	}
	replay := call("POST", "/v1/chat/completions", run, keys[0], 200, "live-run-turn")["harmonika"].(map[string]any)
	if replay["replayed"] != true || replay["status"] != "completed" || replay["approval_required"] != false {
		t.Fatal("replay lost current job state")
	}
	changed := map[string]any{"model": "harmonika-ai", "workspace_id": wid, "messages": []map[string]string{{"role": "user", "content": "different"}}}
	call("POST", "/v1/chat/completions", changed, keys[0], 409, "live-run-turn")
	call("GET", "/v1/coding/jobs/"+jid+"/events?after_sequence=0", nil, keys[0], 200, "")

	eventReq, _ := http.NewRequestWithContext(ctx, "GET", base+"/v1/coding/jobs/"+jid+"/events", nil)
	eventReq.Header.Set("Authorization", "Bearer "+keys[0])
	eventReq.Header.Set("Accept", "text/event-stream")
	eventReq.Header.Set("Last-Event-ID", jid+":1")
	eventRes, err := http.DefaultClient.Do(eventReq)
	if err != nil {
		t.Fatal(err)
	}
	eventRaw, _ := io.ReadAll(io.LimitReader(eventRes.Body, 1024*1024))
	eventRes.Body.Close()
	if eventRes.StatusCode != 200 || !strings.Contains(eventRes.Header.Get("Content-Type"), "text/event-stream") || !strings.Contains(string(eventRaw), "[DONE]") || strings.Contains(string(eventRaw), "id: "+jid+":1\n") {
		t.Fatal("SSE replay contract failed")
	}
	streamBody, _ := json.Marshal(map[string]any{"model": "harmonika-ai", "workspace_id": wid, "stream": true, "messages": []map[string]string{{"role": "user", "content": "Gunakan tool fs_list untuk melihat daftar file workspace saat ini, lalu laporkan nama file yang ditemukan. Jangan mengubah atau menjalankan file."}}})
	streamReq, _ := http.NewRequestWithContext(ctx, "POST", base+"/v1/chat/completions", bytes.NewReader(streamBody))
	streamReq.Header.Set("Authorization", "Bearer "+keys[0])
	streamReq.Header.Set("Content-Type", "application/json")
	streamReq.Header.Set("Idempotency-Key", "live-stream-turn")
	streamRes, err := http.DefaultClient.Do(streamReq)
	if err != nil {
		t.Fatal(err)
	}
	streamRaw, _ := io.ReadAll(io.LimitReader(streamRes.Body, 4*1024*1024))
	streamRes.Body.Close()
	if streamRes.StatusCode != 200 || !strings.Contains(string(streamRaw), "[DONE]") || !strings.Contains(string(streamRaw), "hello.py") || !strings.Contains(string(streamRaw), "response.completed") {
		t.Fatalf("SSE coding chat failed: %.2000s", streamRaw)
	}

 if len(c["skills"].([]any))==38 {
  starter:=map[string]any{"model":"harmonika-ai","workspace_id":wid,"stream":false,"messages":[]map[string]string{{"role":"user","content":"Buat starter server HTTP Python bernama Demo di folder starterdemo memakai template starter yang tersedia, agar program, tes dan README terbentuk sekaligus. Jangan jalankan atau deploy."}}}
  answer:=call("POST","/v1/chat/completions",starter,keys[0],200,"live-phase2-starter")
  meta:=answer["harmonika"].(map[string]any);found:=false
  for _,step:=range meta["steps"].([]any){if step.(map[string]any)["skill_id"]=="scaffold.project"{found=true}}
  if !found{t.Fatalf("automatic Phase2 starter tool was not used: %.1500s",mustJSON(meta))}
  call("POST","/v1/coding/workspaces/"+wid+"/invoke",map[string]any{"skill_id":"code.read","input":map[string]any{"path":"starterdemo/app.py"}},keys[0],200,"")
 }
	report := map[string]any{"ok": true, "checks": []string{"18_schemas", "Bearer_auth", "principal_spoof_blocked", "cross_token_404", "automatic_code_write", "automatic_exec_approval", "real_python_42", "idempotent_replay", "conflicting_turn_409", "event_replay", "SSE_Last_Event_ID", "SSE_automatic_chat"}, "token_ids": ids, "workspace_id": wid}
	raw, _ := json.MarshalIndent(report, "", "  ")
	os.WriteFile("/tmp/harmonika-coding-public-smoke-result.json", raw, 0600)
	t.Log("coding public HTTP + real planner + approved Python passed")
}
func mustJSON(v any) []byte { b, _ := json.Marshal(v); return b }

func TestCodingPhase2Live(t *testing.T) {
 if os.Getenv("HARMONIKA_CODING_PHASE2_LIVE")!="1" {t.Skip("isolated Phase2 acceptance opt-in")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("DB unavailable")};defer db.Close()
 ctx,cancel:=context.WithTimeout(context.Background(),600*time.Second);defer cancel()
 ids:=[]int64{};tokens:=[]map[string]string{}
 defer func(){for _,id:=range ids{db.Exec("DELETE FROM public_ai_requests WHERE token_id=$1",id);db.Exec("DELETE FROM public_ai_tokens WHERE id=$1",id)}}()
 for n:=0;n<2;n++{
  raw:=make([]byte,32);rand.Read(raw);key:="hai_live_"+hex.EncodeToString(raw);var id int64
  if db.QueryRowContext(ctx,`INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,request_limit_per_day) VALUES($1,'isolated-phase2-smoke','isolated-phase2-smoke','chat,models',250) RETURNING id`,hashToken(key)).Scan(&id)!=nil{t.Fatal("test token creation failed")}
  ids=append(ids,id);tokens=append(tokens,map[string]string{"actor":fmt.Sprintf("api-token:%d",id),"key":key})
 }
 file,e:=os.CreateTemp("/opt/harmonika-ai","phase2-bearers-*.json");if e!=nil{t.Fatal("private test input unavailable")};defer os.Remove(file.Name())
 raw,_:=json.Marshal(tokens);if _,e=file.Write(raw);e!=nil{t.Fatal("private test input failed")};file.Close()
 base:=os.Getenv("HARMONIKA_PHASE2_API_BASE");if base==""{t.Fatal("explicit coding HTTPS base required")}
 cmd:=exec.CommandContext(ctx,"python3","/opt/harmonika-ai/coding-phase2-acceptance.py",base,"--report","/tmp/harmonika-phase2-production-smoke-result.json")
 cmd.Env=append(os.Environ(),"HARMONIKA_PHASE2_BEARER_FILE="+file.Name())
 out,e:=cmd.CombinedOutput();if e!=nil{t.Fatalf("Phase2 HTTP acceptance failed: %.14000s",out)}
 var report struct{OK bool `json:"ok"`;Skills []string `json:"phase2_skills_verified"`;Checks []any `json:"checks"`}
 if json.Unmarshal(bytes.TrimSpace(out),&report)!=nil||!report.OK||len(report.Skills)!=20{t.Fatal("incomplete Phase2 verification")}
 t.Logf("Phase2 HTTPS verified20, checks%d, isolated actors%v",len(report.Checks),ids)
}
