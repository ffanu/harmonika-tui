package main
import("context";"crypto/rand";"database/sql";"encoding/hex";"encoding/json";"os";"os/exec";"bytes";"testing";"time")
func TestLegalAllCatalogLive(t *testing.T){
 if os.Getenv("HARMONIKA_LEGAL_ALL_LIVE")!="1"{t.Skip("explicit isolated all-catalog smoke")};db,e:=sql.Open("pgx",os.Getenv("DATABASE_URL"));if e!=nil{t.Fatal("DBunavailable")};defer db.Close();ctx,cancel:=context.WithTimeout(context.Background(),8*time.Minute);defer cancel();ids:=[]int64{};keys:=[]string{}
 defer func(){for _,id:=range ids{db.Exec("DELETE FROM public_ai_requests WHERE token_id=$1",id);db.Exec("DELETE FROM public_ai_tokens WHERE id=$1",id)}}()
 for i:=0;i<2;i++{raw:=make([]byte,32);rand.Read(raw);key:="hai_live_"+hex.EncodeToString(raw);var id int64
  e=db.QueryRowContext(ctx,`INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,tier,request_limit_per_day) VALUES($1,'isolated-legal-all-smoke','isolated-legal-all-smoke','chat,models','public',300) RETURNING id`,hashToken(key)).Scan(&id);if e!=nil{t.Fatal("fixture creation failed")};ids=append(ids,id);keys=append(keys,key);t.Logf("isolated_actor=api-token:%d",id)}
 payload,_:=json.Marshal(map[string]string{"token":keys[0],"other_token":keys[1]});cmd:=exec.CommandContext(ctx,"/opt/harmonika-ai/legal-venv/bin/python","/opt/harmonika-ai/smoke-legal-catalog.py");cmd.Stdin=bytes.NewReader(payload);out,e:=cmd.CombinedOutput();t.Log(string(out));if e!=nil{t.Fatal("all-catalog smoke failed")}
}
