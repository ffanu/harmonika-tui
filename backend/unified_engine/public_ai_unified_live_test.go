package main
import("bytes";"context";"crypto/rand";"database/sql";"encoding/hex";"encoding/json";"os";"os/exec";"testing";"time")
func TestUnifiedFoundationLive(t *testing.T){
 if os.Getenv("HARMONIKA_UNIFIED_LIVE")!="1"{t.Skip("explicit isolated HTTPS acceptance")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("db_unavailable")};defer db.Close();ctx,cancel:=context.WithTimeout(context.Background(),8*time.Minute);defer cancel();ids:=[]int64{};keys:=[]string{}
 defer func(){
  raw,_:=json.Marshal(map[string]any{"actor_ids":ids});cmd:=exec.Command("/opt/harmonika-ai/builder-venv/bin/python","/opt/harmonika-ai/cleanup-unified.py");cmd.Stdin=bytes.NewReader(raw);out,e:=cmd.CombinedOutput();t.Log(string(out));if e!=nil{t.Error("fixture state cleanup failed")}
  for _,id:=range ids{if _,e:=db.Exec("DELETE FROM public_ai_requests WHERE token_id=$1",id);e!=nil{t.Error("quota fixture cleanup")};if _,e:=db.Exec("DELETE FROM public_ai_tokens WHERE id=$1",id);e!=nil{t.Error("token fixture cleanup")}}
 }()
 for i:=0;i<4;i++{
  b:=make([]byte,32);if _,e:=rand.Read(b);e!=nil{t.Fatal("random_failed")};key:="hai_live_"+hex.EncodeToString(b);var id int64
  e=db.QueryRowContext(ctx,`INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,tier,request_limit_per_day) VALUES($1,'isolated-unified-foundation-smoke','isolated-unified-foundation-smoke','chat,models','public',400) RETURNING id`,hashToken(key)).Scan(&id)
  if e!=nil{t.Fatal("fixture_token_creation")};ids=append(ids,id);keys=append(keys,key)
 }
 raw,_:=json.Marshal(map[string]any{"tokens":keys,"actor_ids":ids});cmd:=exec.CommandContext(ctx,"/opt/harmonika-ai/builder-venv/bin/python","/opt/harmonika-ai/smoke-unified.py");cmd.Stdin=bytes.NewReader(raw);out,e:=cmd.CombinedOutput();t.Log(string(out));if e!=nil{t.Fatal("unified_https_smoke_failed")}
}
func TestUnifiedFixtureCleanup(t *testing.T){
 if os.Getenv("HARMONIKA_UNIFIED_LIVE")!="1"{t.Skip("explicit isolated fixture cleanup check")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("db_unavailable")};defer db.Close();var n int
 if e:=db.QueryRow("SELECT count(*) FROM public_ai_tokens WHERE owner_chat_id='isolated-unified-foundation-smoke'").Scan(&n);e!=nil||n!=0{t.Fatal("fixture token rows remain")}
}
