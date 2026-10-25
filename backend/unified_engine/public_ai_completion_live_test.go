package main
import("bytes";"context";"crypto/rand";"database/sql";"encoding/hex";"encoding/json";"io";"net/http";"os";"os/exec";"testing";"time")
func TestUnifiedCompletionAcceptance(t *testing.T){
 stage:=os.Getenv("HARMONIKA_COMPLETION_STAGE")=="1";live:=os.Getenv("HARMONIKA_COMPLETION_LIVE")=="1";if !stage&&!live{t.Skip("explicit isolated acceptance")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("db_unavailable")};defer db.Close();ctx,cancel:=context.WithTimeout(context.Background(),8*time.Minute);defer cancel();ids:=[]int64{};keys:=[]string{}
 defer func(){
  raw,_:=json.Marshal(map[string]any{"actor_ids":ids});cmd:=exec.Command("/opt/harmonika-ai/builder-venv/bin/python","/opt/harmonika-ai/cleanup-completion.py");cmd.Stdin=bytes.NewReader(raw);out,e:=cmd.CombinedOutput();t.Log(string(out));if e!=nil{t.Error("fixture_state_cleanup_failed")}
  for _,id:=range ids{if _,e:=db.Exec("DELETE FROM public_ai_requests WHERE token_id=$1",id);e!=nil{t.Error("quota_fixture_cleanup")};if _,e:=db.Exec("DELETE FROM public_ai_tokens WHERE id=$1",id);e!=nil{t.Error("token_fixture_cleanup")}}
 }()
 for i:=0;i<4;i++{
  b:=make([]byte,32);if _,e:=rand.Read(b);e!=nil{t.Fatal("random_failed")};key:="hai_live_"+hex.EncodeToString(b);var id int64;scopes:="chat,models";if i==1||i==2{scopes+=",secrets:read,secrets:write"}
  e=db.QueryRowContext(ctx,`INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,tier,request_limit_per_day) VALUES($1,'isolated-unified-completion-smoke','isolated-unified-completion-smoke',$2,'public',400) RETURNING id`,hashToken(key),scopes).Scan(&id)
  if e!=nil{t.Fatal("fixture_token_creation")};ids=append(ids,id);keys=append(keys,key)
 }
 script:="/opt/harmonika-ai/smoke-completion.py";if stage{script="/opt/harmonika-ai/unified-completion-stage/harmonika-completion-stage-smoke.py"}
 raw,_:=json.Marshal(map[string]any{"tokens":keys,"actor_ids":ids});cmd:=exec.CommandContext(ctx,"/opt/harmonika-ai/builder-venv/bin/python",script);cmd.Stdin=bytes.NewReader(raw);out,e:=cmd.CombinedOutput();t.Log(string(out));if e!=nil{t.Fatal("completion_acceptance_failed")}
 if live{
  req,_:=http.NewRequestWithContext(ctx,"POST","https://chat.harmonika.id/v1/unified/ticket",bytes.NewBufferString("{}"));req.Header.Set("Authorization","Bearer "+keys[3]);req.Header.Set("Content-Type","application/json");req.Header.Set("Origin","https://chat.harmonika.id")
  client:=http.Client{Timeout:20*time.Second,CheckRedirect:func(_ *http.Request,_ []*http.Request)error{return http.ErrUseLastResponse}};res,e:=client.Do(req);if e!=nil{t.Fatal("revocation_ticket_setup")};defer res.Body.Close();payload,e:=io.ReadAll(io.LimitReader(res.Body,65536));if e!=nil||res.StatusCode!=200{t.Fatal("revocation_ticket_creation")};ticket:=map[string]any{};if json.Unmarshal(payload,&ticket)!=nil{t.Fatal("ticket_json")}
  if _,e:=db.ExecContext(ctx,"UPDATE public_ai_tokens SET revoked_at=now() WHERE id=$1",ids[3]);e!=nil{t.Fatal("fixture_revocation")}
  raw,_=json.Marshal(map[string]any{"revoked_ticket":ticket["ticket"]});cmd=exec.CommandContext(ctx,"/opt/harmonika-ai/builder-venv/bin/python",script);cmd.Stdin=bytes.NewReader(raw);out,e=cmd.CombinedOutput();t.Log(string(out));if e!=nil{t.Error("revoked_ticket_was_accepted")}
 }
}
func TestUnifiedCompletionCleanup(t *testing.T){
 if os.Getenv("HARMONIKA_COMPLETION_STAGE")!="1"&&os.Getenv("HARMONIKA_COMPLETION_LIVE")!="1"{t.Skip("explicit cleanup check")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("db_unavailable")};defer db.Close();var n int
 if e:=db.QueryRow("SELECT count(*) FROM public_ai_tokens WHERE owner_chat_id='isolated-unified-completion-smoke'").Scan(&n);e!=nil||n!=0{t.Fatal("fixture_token_rows_remain")}
}
