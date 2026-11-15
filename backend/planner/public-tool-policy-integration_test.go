package main
import("encoding/json";"net/http";"net/http/httptest";"net/url";"os";"strings";"testing")
func TestLevel12ToolPolicyHTTPBoundary(t *testing.T){
 if _,err:=os.Stat("/etc/harmonika-ai/internal-agent.token");err!=nil{t.Skip("requires existing private integration reference on staged backend")}
 for _,c:=range []struct{code string;want int;retry bool}{{"tool_secret_reference_required",400,false},{"invalid_tool_history",400,false},{"context_length_exceeded",413,false},{"tool_input_complexity",400,false},{"private_upstream_error",502,false}}{
  upstream:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){w.WriteHeader(400);json.NewEncoder(w).Encode(map[string]any{"error":map[string]any{"code":c.code,"message":"EXAM-SENTINEL-private-detail"}})}));u,_:=url.Parse(upstream.URL);a:=&app{engine:u};rec:=httptest.NewRecorder();req:=httptest.NewRequest("POST","/v1/chat/completions",nil);a.publicAIToolChat(rec,req,publicAIToken{Tier:"internal"},publicAIChatCompletionRequest{});upstream.Close()
  if rec.Code!=c.want{t.Fatalf("status mismatch for safe policy enum: %d",rec.Code)}
  if rec.Header().Get("Retry-After")!=""{t.Fatal("policy rejection must not invite retry")}
  if strings.Contains(rec.Body.String(),"EXAM-SENTINEL"){t.Fatal("upstream detail leaked")}
  var body struct{Error struct{Code string `json:"code"`}};if json.Unmarshal(rec.Body.Bytes(),&body)!=nil{t.Fatal("invalid error envelope")};want:=c.code;if c.want==502{want="engine_error"};if body.Error.Code!=want{t.Fatal("safe enum not preserved")}
 }
}
