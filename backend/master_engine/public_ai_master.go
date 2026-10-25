package main

import (
 "bytes"
 "context"
 "encoding/json"
 "fmt"
 "io"
 "net/http"
 "os"
 "regexp"
 "strings"
 "time"
)
var masterSkillID=regexp.MustCompile(`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$`)
var masterUUID=regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
type masterContext struct {Engine string `json:"engine,omitempty"`; ProjectID string `json:"project_id,omitempty"`; WorkspaceID string `json:"workspace_id,omitempty"`; ExpectedRevision *int `json:"expected_revision,omitempty"`; Overwrite bool `json:"overwrite,omitempty"`}
type masterInvocation struct{SkillID string `json:"skill_id"`;Input map[string]any `json:"input"`;Context masterContext `json:"context,omitempty"`}
func masterDecode(raw []byte)(masterInvocation,error){if e:=coreUniqueJSON(raw);e!=nil{return masterInvocation{},e};var v masterInvocation;d:=json.NewDecoder(bytes.NewReader(raw));d.UseNumber();d.DisallowUnknownFields();e:=d.Decode(&v);if e!=nil{return v,e};var extra any;if d.Decode(&extra)!=io.EOF{return v,fmt.Errorf("invalid_json")};if !masterSkillID.MatchString(v.SkillID)||len(v.SkillID)>100||v.Input==nil{return v,fmt.Errorf("invalid_request")};return v,nil}
func masterEngineRoute(v masterInvocation,registered string)(string,map[string]any,error){
 c:=v.Context;if (v.SkillID=="evidence.chain.custody"||v.SkillID=="evidence.store.secure"||v.SkillID=="debug.attach"||v.SkillID=="debug.breakpoint"||v.SkillID=="test.run"||v.SkillID=="test.single"||v.SkillID=="test.coverage")&&c.Engine==""{return "",nil,fmt.Errorf("explicit_engine_context_required")};engine:=c.Engine;if engine==""{engine=registered};if engine!=registered{return "",nil,fmt.Errorf("engine_context_mismatch")}
 switch engine{
 case "builder":if !masterUUID.MatchString(c.ProjectID)||c.WorkspaceID!=""{return "",nil,fmt.Errorf("builder_project_required")};return "/v1/builder/projects/"+c.ProjectID+"/invoke",map[string]any{"skill_id":v.SkillID,"input":v.Input,"expected_revision":c.ExpectedRevision,"overwrite":c.Overwrite},nil
 case "coding":if !masterUUID.MatchString(c.WorkspaceID)||c.ProjectID!=""||c.Overwrite||c.ExpectedRevision!=nil{return "",nil,fmt.Errorf("coding_workspace_required")};return "/v1/coding/workspaces/"+c.WorkspaceID+"/invoke",map[string]any{"skill_id":v.SkillID,"input":v.Input},nil
 case "legal","telco":if c.ProjectID!=""||c.WorkspaceID!=""||c.Overwrite||c.ExpectedRevision!=nil{return "",nil,fmt.Errorf("invalid_engine_context")};return "/v1/"+engine+"/invoke",map[string]any{"skill_id":v.SkillID,"input":v.Input},nil
 };return "",nil,fmt.Errorf("engine_context_required")
}
func masterUpstream(ctx context.Context,t publicAIToken,method,path string,raw []byte)(*http.Response,error){
 secret,e:=os.ReadFile("/etc/harmonika-ai/master-engine.token");if e!=nil{return nil,fmt.Errorf("master_unavailable")};req,e:=http.NewRequestWithContext(ctx,method,"http://127.0.0.1:18193"+path,bytes.NewReader(raw));if e!=nil{return nil,e};req.Header.Set("Content-Type","application/json");req.Header.Set("X-Harmonika-Master-Secret",strings.TrimSpace(string(secret)));req.Header.Set("X-Harmonika-Principal",fmt.Sprintf("api-token:%d",t.ID));return (&http.Client{CheckRedirect:func(_ *http.Request,_ []*http.Request)error{return http.ErrUseLastResponse}}).Do(req)
}
func(a *app)publicMasterSkills(w http.ResponseWriter,r *http.Request){
 path:="";if r.Method=="GET"{switch{case r.URL.Path=="/v1/skills/capabilities":path="/capabilities";case r.URL.Path=="/v1/skills":path="/skills";case strings.HasPrefix(r.URL.Path,"/v1/skills/")&&masterSkillID.MatchString(strings.TrimPrefix(r.URL.Path,"/v1/skills/")):path="/skills/"+strings.TrimPrefix(r.URL.Path,"/v1/skills/")}}else if r.Method=="POST"&&r.URL.Path=="/v1/skills/invoke"&&r.URL.RawQuery==""{path="/invoke"}
 if path==""{jsonOut(w,404,map[string]any{"error":map[string]any{"code":"not_found"}});return}
 t,ok:=a.telcoToken(w,r);if !ok{return};scope:=false;for _,s:=range strings.Split(t.Scopes,","){if s=="chat"||s=="skills"{scope=true}};if !scope{jsonOut(w,403,map[string]any{"error":map[string]any{"code":"skills_scope_required"}});return}
 raw,e:=io.ReadAll(http.MaxBytesReader(w,r.Body,1048576));if e!=nil{jsonOut(w,413,map[string]any{"error":map[string]any{"code":"input_too_large"}});return}
 if r.Method=="POST"{
  v,e:=masterDecode(raw);if e!=nil{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"invalid_request"}});return}
  if ids,_,err:=coreCatalogMap();err==nil&&ids[v.SkillID]{a.publicCoreSkills(w,coreForward(r,"/v1/core/invoke",raw));return}
  if unifiedCatalogIDs()[v.SkillID]{a.publicUnifiedAPI(w,coreForward(r,"/v1/unified/invoke",raw));return}
  // Trusted map contains exact existing IDs only. No prefix-based or semantic aliases.
  routesRaw,e:=os.ReadFile("/opt/harmonika-ai/master_engine/existing_routes.json");if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"catalog_unavailable"}});return};routes:=map[string]string{};if json.Unmarshal(routesRaw,&routes)!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"catalog_unavailable"}});return}
  if engine,exists:=routes[v.SkillID];exists{
   target,body,err:=masterEngineRoute(v,engine);if err!=nil{jsonOut(w,409,map[string]any{"error":map[string]any{"code":err.Error()}});return};encoded,_:=json.Marshal(body);forward:=r.Clone(r.Context());u:=*r.URL;u.Path=target;u.RawPath="";u.RawQuery="";forward.URL=&u;forward.Body=io.NopCloser(bytes.NewReader(encoded));forward.ContentLength=int64(len(encoded));forward.Header=r.Header.Clone();forward.Header.Set("Content-Type","application/json")
   switch engine{case "builder":a.publicBuilderAPI(w,forward);case "coding":a.publicCodingAPI(w,forward);case "legal":a.publicLegalAPI(w,forward);case "telco":a.publicTelcoAPI(w,forward)};return
  }
  if v.Context!=(masterContext{}){jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unexpected_context"}});return};raw,_=json.Marshal(map[string]any{"skill_id":v.SkillID,"input":v.Input})
  if status,code:=a.reservePublicAIQuota(r.Context(),&t,r);status!=0{jsonOut(w,status,map[string]any{"error":map[string]any{"code":code}});return}
 }
 if r.URL.RawQuery!=""{if path!="/skills"{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unsupported_query"}});return};path+="?"+r.URL.Query().Encode()}
 ctx,cancel:=context.WithTimeout(r.Context(),50*time.Second);defer cancel();res,e:=masterUpstream(ctx,t,r.Method,path,raw);if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"master_unavailable"}});return};defer res.Body.Close();forwardTelcoResponse(w,res)
}
func publicMasterReady()bool{
 if _,e:=os.Stat("/var/lib/harmonika-master/ready.json");e!=nil{return false}
 c:=http.Client{Timeout:300*time.Millisecond};r,e:=c.Get("http://127.0.0.1:18193/healthz");if e!=nil{return false};defer r.Body.Close();return r.StatusCode==200
}
