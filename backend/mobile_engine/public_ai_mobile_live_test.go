package main
import("bytes";"context";"crypto/rand";"database/sql";"encoding/hex";"encoding/json";"os";"os/exec";"testing";"time")
func TestMobileBuilderLive(t *testing.T){
 if os.Getenv("HARMONIKA_MOBILE_LIVE")!="1"{t.Skip("explicit isolated HTTPS smoke")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("database_unavailable")};defer db.Close()
 ctx,cancel:=context.WithTimeout(context.Background(),5*time.Minute);defer cancel();ids:=[]int64{};keys:=[]string{}
 defer func(){for _,id:=range ids{if _,e:=db.Exec("DELETE FROM public_ai_requests WHERE token_id=$1",id);e!=nil{t.Error("fixture quota cleanup failed")};if _,e:=db.Exec("DELETE FROM public_ai_tokens WHERE id=$1",id);e!=nil{t.Error("fixture token cleanup failed")}}}()
 for i:=0;i<4;i++{
  raw:=make([]byte,32);if _,e:=rand.Read(raw);e!=nil{t.Fatal("random_failed")};key:="hai_live_"+hex.EncodeToString(raw);var id int64
  e=db.QueryRowContext(ctx,`INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,tier,request_limit_per_day) VALUES($1,'isolated-mobile-builder-smoke','isolated-mobile-builder-smoke','chat,models','public',150) RETURNING id`,hashToken(key)).Scan(&id)
  if e!=nil{t.Fatal("fixture_creation_failed")};ids=append(ids,id);keys=append(keys,key)
 }
 payload,_:=json.Marshal(map[string]any{"tokens":keys});cmd:=exec.CommandContext(ctx,"/opt/harmonika-ai/builder-venv/bin/python","/opt/harmonika-ai/smoke-mobile-skills.py");cmd.Stdin=bytes.NewReader(payload);out,e:=cmd.CombinedOutput();t.Log(string(out));if e!=nil{t.Fatal("mobile_https_smoke_failed")}
}

func TestMobileFixtureCleanup(t *testing.T){
 if os.Getenv("HARMONIKA_MOBILE_LIVE")!="1"{t.Skip("explicit smoke cleanup verification")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("database_unavailable")};defer db.Close()
 var count int;if e:=db.QueryRow("SELECT count(*) FROM public_ai_tokens WHERE owner_chat_id='isolated-mobile-builder-smoke'").Scan(&count);e!=nil||count!=0{t.Fatal("fixture tokens remain")}
}
