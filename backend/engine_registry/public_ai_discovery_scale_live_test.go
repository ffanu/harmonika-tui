package main
import("bytes";"context";"crypto/rand";"database/sql";"encoding/hex";"encoding/json";"os";"os/exec";"testing";"time")
func TestDiscoveryScaleLive(t *testing.T){
 if os.Getenv("HARMONIKA_DISCOVERY_LIVE")!="1"{t.Skip("explicit isolated discovery acceptance")}
 db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("db_unavailable")};defer db.Close()
 ctx,cancel:=context.WithTimeout(context.Background(),3*time.Minute);defer cancel()
 b:=make([]byte,32);if _,e=rand.Read(b);e!=nil{t.Fatal("random_failed")};key:="hai_live_"+hex.EncodeToString(b);var id int64
 e=db.QueryRowContext(ctx,`INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,tier,request_limit_per_day) VALUES($1,'isolated-discovery-scale-smoke','isolated-discovery-scale-smoke','chat,models','public',80) RETURNING id`,hashToken(key)).Scan(&id)
 if e!=nil{t.Fatal("fixture_token_creation")}
 defer func(){if _,e=db.Exec("DELETE FROM public_ai_requests WHERE token_id=$1",id);e!=nil{t.Error("fixture_quota_cleanup")};if _,e=db.Exec("DELETE FROM public_ai_tokens WHERE id=$1",id);e!=nil{t.Error("fixture_token_cleanup")}}()
 raw,_:=json.Marshal(map[string]any{"token":key,"actor_id":id});cmd:=exec.CommandContext(ctx,"/opt/harmonika-ai/builder-venv/bin/python","/opt/harmonika-ai/smoke-discovery-scale.py");cmd.Stdin=bytes.NewReader(raw);out,e:=cmd.CombinedOutput();t.Log(string(out));if e!=nil{t.Fatal("discovery_acceptance_failed")}
}
func TestDiscoveryScaleCleanup(t *testing.T){
 if os.Getenv("HARMONIKA_DISCOVERY_LIVE")!="1"{t.Skip("explicit fixture cleanup check")};db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("db_unavailable")};defer db.Close();var n int
 if e=db.QueryRow("SELECT count(*) FROM public_ai_tokens WHERE owner_chat_id='isolated-discovery-scale-smoke'").Scan(&n);e!=nil||n!=0{t.Fatal("fixture_token_cleanup_not_complete")}
}
