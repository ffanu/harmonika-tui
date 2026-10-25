package main
import("bytes";"context";"crypto/rand";"database/sql";"encoding/hex";"encoding/json";"io";"net/http";"net/http/httptest";"net/url";"os";"strings";"testing";"time")
// Explicitly opt-in. Creates only its own short-lived key/request rows; never
// reads a customer's conversation or rotates existing keys.
func TestAutoSkillsLive(t *testing.T){
 if os.Getenv("HARMONIKA_AUTO_SKILLS_LIVE")!="1"{t.Skip("live opt-in only")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("DB connection unavailable")};defer db.Close()
 ctx,cancel:=context.WithTimeout(context.Background(),240*time.Second);defer cancel()
 secret:=make([]byte,32);if _,e=rand.Read(secret);e!=nil{t.Fatal("random unavailable")};key:="hai_live_"+hex.EncodeToString(secret)
 var id int64
 e=db.QueryRowContext(ctx,`INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,request_limit_per_day) VALUES($1,'isolated-auto-skills-smoke','isolated-auto-skills-smoke','chat,models',20) RETURNING id`,hashToken(key)).Scan(&id)
 if e!=nil{t.Fatal("test key setup failed")}
 defer func(){db.Exec(`DELETE FROM public_ai_requests WHERE token_id=$1`,id);db.Exec(`DELETE FROM public_ai_tokens WHERE id=$1`,id)}()
 engine,_:=url.Parse("http://127.0.0.1:17890");a:=&app{db:db,engine:engine}
 server:=httptest.NewServer(http.HandlerFunc(a.publicAIChatCompletions));defer server.Close()
 base:=os.Getenv("HARMONIKA_AUTO_SKILLS_BASE");if base==""{base=server.URL}
 send:=func(body string,auth string)(int,string,time.Duration){began:=time.Now();req,_:=http.NewRequestWithContext(ctx,"POST",base+"/v1/chat/completions",strings.NewReader(body));req.Header.Set("Content-Type","application/json");if auth!=""{req.Header.Set("Authorization","Bearer "+auth)};res,e:=http.DefaultClient.Do(req);if e!=nil{t.Fatal("HTTP test failed")};defer res.Body.Close();raw,_:=io.ReadAll(io.LimitReader(res.Body,1<<20));return res.StatusCode,string(raw),time.Since(began)}
 code,_,_:=send(`{"messages":[{"role":"user","content":"halo"}]}`,"");if code!=401{t.Fatal("auth denial",code)}
 body:=`{"model":"harmonika-ai","messages":[{"role":"user","content":"Lakukan audit SEO singkat pada https://harmonika.id/ dengan satu pemeriksaan page audit saja. Sebutkan dua temuan dari data nyata."}]}`
 code,raw,duration:=send(body,key);var out struct{Harmonika struct{Auto bool `json:"automatic_skills"`;N int `json:"tool_executions"`} `json:"harmonika"`;Choices []struct{Message struct{Content string `json:"content"`} `json:"message"`} `json:"choices"`}
 if code!=200 || json.Unmarshal([]byte(raw),&out)!=nil || !out.Harmonika.Auto || out.Harmonika.N<1 || len(out.Choices)!=1 || out.Choices[0].Message.Content=="" {t.Fatalf("automatic SEO failed HTTP %d (body redacted)",code)}
 t.Logf("automatic SEO HTTP200 actual_tool_executions=%d duration=%s",out.Harmonika.N,duration.Round(time.Millisecond))
 t.Logf("answer excerpt: %.350s",out.Choices[0].Message.Content)
 body=`{"model":"harmonika-ai","stream":true,"messages":[{"role":"user","content":"Audit SEO https://harmonika.id/ memakai satu page audit, berikan dua temuan singkat."}]}`
 code,raw,duration=send(body,key)
 if code!=200 || !strings.Contains(raw,"data: [DONE]") || !strings.Contains(raw,`"finish_reason":"stop"`) || strings.Contains(raw,`"error"`){t.Fatalf("SSE failed %d",code)}
 t.Logf("SSE HTTP200 complete duration=%s",duration.Round(time.Millisecond))
 body=`{"model":"harmonika-ai","messages":[{"role":"user","content":"Jawab hanya kata OK."}]}`
 code,raw,duration=send(body,key)
 if code!=200 || !bytes.Contains([]byte(raw),[]byte(`"content"`)) || strings.Contains(raw,`"automatic_skills"`){t.Fatalf("normal chat regression %d",code)}
 t.Logf("ordinary chat HTTP200 existing route duration=%s",duration.Round(time.Millisecond))
 req,_:=http.NewRequestWithContext(ctx,"GET",base+"/v1/public-ai/capabilities",nil);req.Header.Set("Authorization","Bearer "+key)
 if os.Getenv("HARMONIKA_AUTO_SKILLS_BASE")!=""{res,e:=http.DefaultClient.Do(req);if e!=nil{t.Fatal("capability request failed")};raw,_:=io.ReadAll(io.LimitReader(res.Body,1<<20));res.Body.Close();var caps struct{Features map[string]any `json:"features"`};json.Unmarshal(raw,&caps);if res.StatusCode!=200 || caps.Features["automatic_skills"]!=true || caps.Features["seo_ga4_connected"]!=false {t.Fatal("capability mismatch")};t.Log("production capability automatic_skills=true; Google account adapters disconnected")}

}
