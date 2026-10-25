package main

import (
 "bytes"
 "context"
 "encoding/json"
 "fmt"
 "io"
 "net/http"
 "os"
 "strings"
 "time"
 "unicode/utf8"
)
func coreUniqueJSON(raw []byte)error{
 if !utf8.Valid(raw){return fmt.Errorf("invalid_utf8")};d:=json.NewDecoder(bytes.NewReader(raw));d.UseNumber();nodes:=0
 var walk func(int)error
 walk=func(depth int)error{
  nodes++;if depth>20||nodes>20000{return fmt.Errorf("input_complexity")}
  t,e:=d.Token();if e!=nil{return e};delim,container:=t.(json.Delim);if !container{return nil}
  switch delim{
  case '{':seen:=map[string]bool{};for d.More(){k,e:=d.Token();if e!=nil{return e};name,ok:=k.(string);if !ok||seen[name]{return fmt.Errorf("duplicate_json_key")};seen[name]=true;if e=walk(depth+1);e!=nil{return e}};t,e=d.Token();if e!=nil||t!=json.Delim('}'){return fmt.Errorf("invalid_json")}
  case '[':for d.More(){if e=walk(depth+1);e!=nil{return e}};t,e=d.Token();if e!=nil||t!=json.Delim(']'){return fmt.Errorf("invalid_json")}
  default:return fmt.Errorf("invalid_json")
  };return nil
 }
 if e:=walk(0);e!=nil{return e};if _,e:=d.Token();e!=io.EOF{return fmt.Errorf("trailing_json")};return nil
}
func coreCatalogMap()(map[string]bool,map[string]string,error){
 raw,e:=os.ReadFile("/opt/harmonika-ai/core_engine/catalog_source.json");if e!=nil{return nil,nil,e}
 var source struct{Skills []struct{ID string `json:"id"`} `json:"skills"`};if e=json.Unmarshal(raw,&source);e!=nil{return nil,nil,e};ids:=map[string]bool{};for _,x:=range source.Skills{ids[x.ID]=true}
 raw,e=os.ReadFile("/opt/harmonika-ai/core_engine/aliases.json");if e!=nil{return nil,nil,e};aliases:=map[string]string{};e=json.Unmarshal(raw,&aliases);return ids,aliases,e
}
func coreDelegation(v masterInvocation,target string)(string,map[string]any,error){
 c:=v.Context
 if c.Engine!=""&&c.Engine!="coding"{return "",nil,fmt.Errorf("engine_context_mismatch")}
 if !masterUUID.MatchString(c.WorkspaceID)||c.ProjectID!=""||c.ExpectedRevision!=nil||c.Overwrite{return "",nil,fmt.Errorf("coding_workspace_required")}
 input:=v.Input
 if v.SkillID=="system.info"{
  if len(input)!=0{return "",nil,fmt.Errorf("invalid_input")}
  input=map[string]any{"code":`import json,platform,os,shutil; d=shutil.disk_usage('.'); print(json.dumps({'scope':'owned_sandbox','os':platform.system(),'architecture':platform.machine(),'cpu_count':os.cpu_count(),'disk':{'total':d.total,'free':d.free}}))`,"timeout_seconds":15}
 }
 return "/v1/coding/workspaces/"+c.WorkspaceID+"/invoke",map[string]any{"skill_id":target,"input":input},nil
}
func coreChatBody(v masterInvocation)([]byte,error){
 if v.Context!=(masterContext{})&&v.Context!=(masterContext{Engine:"core"}){return nil,fmt.Errorf("unexpected_context")}
 messages:=[]map[string]string{}
 if v.SkillID=="llm.complete"{
  if len(v.Input)!=1{return nil,fmt.Errorf("invalid_input")};prompt,ok:=v.Input["prompt"].(string);if !ok||strings.TrimSpace(prompt)==""||utf8.RuneCountInString(prompt)>16000{return nil,fmt.Errorf("invalid_input")};messages=append(messages,map[string]string{"role":"user","content":prompt})
 }else{
  if len(v.Input)!=1{return nil,fmt.Errorf("invalid_input")};rows,ok:=v.Input["messages"].([]any);if !ok||len(rows)<1||len(rows)>20{return nil,fmt.Errorf("invalid_input")}
  for _,row:=range rows{m,ok:=row.(map[string]any);if !ok||len(m)!=2{return nil,fmt.Errorf("invalid_input")};role,ok:=m["role"].(string);if !ok||(role!="system"&&role!="user"&&role!="assistant"){return nil,fmt.Errorf("invalid_role")};content,ok:=m["content"].(string);if !ok||strings.TrimSpace(content)==""||utf8.RuneCountInString(content)>16000{return nil,fmt.Errorf("invalid_input")};messages=append(messages,map[string]string{"role":role,"content":content})}
 }
 return json.Marshal(map[string]any{"model":"harmonika-ai","messages":messages,"stream":false})
}
func coreForward(r *http.Request,path string,body []byte)*http.Request{
 f:=r.Clone(r.Context());u:=*r.URL;u.Path=path;u.RawPath="";u.RawQuery="";f.URL=&u;f.Body=io.NopCloser(bytes.NewReader(body));f.ContentLength=int64(len(body));f.Header=r.Header.Clone();f.Header.Set("Content-Type","application/json");return f
}
func coreServiceRequest(ctx context.Context,t publicAIToken,method,path string,raw []byte,idem string)(*http.Response,error){
 secret,e:=os.ReadFile("/etc/harmonika-ai/core-engine.token");if e!=nil{return nil,fmt.Errorf("core_unavailable")}
 req,e:=http.NewRequestWithContext(ctx,method,"http://127.0.0.1:18195"+path,bytes.NewReader(raw));if e!=nil{return nil,e}
 req.Header.Set("Content-Type","application/json");req.Header.Set("X-Harmonika-Core-Secret",strings.TrimSpace(string(secret)));req.Header.Set("X-Harmonika-Principal",fmt.Sprintf("api-token:%d",t.ID));if idem!=""{req.Header.Set("Idempotency-Key",idem)}
 return (&http.Client{CheckRedirect:func(_ *http.Request,_ []*http.Request)error{return http.ErrUseLastResponse}}).Do(req)
}
func(a *app)publicCoreSkills(w http.ResponseWriter,r *http.Request){
 path:=""
 if r.Method=="GET"{switch{case r.URL.Path=="/v1/core/capabilities":path="/capabilities";case r.URL.Path=="/v1/core/skills":path="/skills";case strings.HasPrefix(r.URL.Path,"/v1/core/skills/")&&masterSkillID.MatchString(strings.TrimPrefix(r.URL.Path,"/v1/core/skills/")):path="/skills/"+strings.TrimPrefix(r.URL.Path,"/v1/core/skills/")}}
 if r.Method=="POST"&&r.URL.Path=="/v1/core/invoke"{path="/invoke"}
 if path==""{jsonOut(w,404,map[string]any{"error":map[string]any{"code":"not_found"}});return}
 t,ok:=a.telcoToken(w,r);if !ok{return};scope:=false;for _,s:=range strings.Split(t.Scopes,","){if s=="chat"||s=="skills"||s=="core"{scope=true}};if !scope{jsonOut(w,403,map[string]any{"error":map[string]any{"code":"core_scope_required"}});return}
 raw,e:=io.ReadAll(http.MaxBytesReader(w,r.Body,1048576));if e!=nil{jsonOut(w,413,map[string]any{"error":map[string]any{"code":"input_too_large"}});return}
 if r.Method=="POST"{
  if r.URL.RawQuery!=""||coreUniqueJSON(raw)!=nil{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"invalid_json"}});return}
  v,e:=masterDecode(raw);if e!=nil{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"invalid_request"}});return};ids,aliases,e:=coreCatalogMap();if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"core_catalog_unavailable"}});return};if !ids[v.SkillID]{jsonOut(w,404,map[string]any{"error":map[string]any{"code":"unknown_skill"}});return}
  w.Header().Set("X-Harmonika-Requested-Skill",v.SkillID)
  if target,exists:=aliases[v.SkillID];exists{
   dest,body,err:=coreDelegation(v,target);if err!=nil{jsonOut(w,409,map[string]any{"error":map[string]any{"code":err.Error()}});return};b,_:=json.Marshal(body);w.Header().Set("X-Harmonika-Resolved-Skill",target);a.publicCodingAPI(w,coreForward(r,dest,b));return
  }
  if v.SkillID=="llm.chat"||v.SkillID=="llm.complete"{
   b,err:=coreChatBody(v);if err!=nil{jsonOut(w,400,map[string]any{"error":map[string]any{"code":err.Error()}});return};a.publicAIChatCompletions(w,coreForward(r,"/v1/chat/completions",b));return
  }
  if v.Context!=(masterContext{})&&v.Context!=(masterContext{Engine:"core"}){jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unexpected_context"}});return}
  raw,_=json.Marshal(map[string]any{"skill_id":v.SkillID,"input":v.Input})
  if status,code:=a.reservePublicAIQuota(r.Context(),&t,r);status!=0{jsonOut(w,status,map[string]any{"error":map[string]any{"code":code}});return}
 }
 if r.URL.RawQuery!=""{if path!="/skills"{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unsupported_query"}});return};path+="?"+r.URL.Query().Encode()}
 ctx,cancel:=context.WithTimeout(r.Context(),25*time.Second);defer cancel();res,e:=coreServiceRequest(ctx,t,r.Method,path,raw,r.Header.Get("Idempotency-Key"));if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"core_unavailable"}});return};defer res.Body.Close();forwardTelcoResponse(w,res)
}
func publicCoreReady()bool{
 if _,e:=os.Stat("/var/lib/harmonika-core/ready.json");e!=nil{return false};c:=http.Client{Timeout:300*time.Millisecond};r,e:=c.Get("http://127.0.0.1:18195/healthz");if e!=nil{return false};defer r.Body.Close();return r.StatusCode==200
}
