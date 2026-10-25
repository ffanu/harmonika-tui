package main

import (
 "bytes"
 "context"
 "encoding/json"
 "io"
 "net/http"
 "net/http/httputil"
 "os"
 "regexp"
 "strings"
 "time"
 "fmt"
)
var unifiedUUIDPart=`[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}`
var unifiedJob=regexp.MustCompile(`^/v1/unified/jobs/`+unifiedUUIDPart+`(/events|/stop)?$`)
var unifiedSession=regexp.MustCompile(`^/v1/unified/sessions/`+unifiedUUIDPart+`$`)
func unifiedTarget(method,path string)(string,bool){
 if method=="GET"{
  if path=="/v1/unified/ws"||path=="/v1/unified/capabilities"||path=="/v1/unified/skills"||unifiedSession.MatchString(path)||(unifiedJob.MatchString(path)&&!strings.HasSuffix(path,"/stop")){return strings.TrimPrefix(path,"/v1/unified"),true}
  if strings.HasPrefix(path,"/v1/unified/skills/"){id:=strings.TrimPrefix(path,"/v1/unified/skills/");if len(id)<=100&&masterSkillID.MatchString(id){return "/skills/"+id,true}}
 }
 if method=="POST"&&(path=="/v1/unified/graphql"||path=="/v1/unified/ticket"||path=="/v1/unified/bus/events"||path=="/v1/unified/bus/replay"||path=="/v1/unified/knowledge/query"||path=="/v1/unified/secrets"||path=="/v1/unified/invoke"||path=="/v1/unified/jobs"||path=="/v1/unified/sessions"||(unifiedJob.MatchString(path)&&strings.HasSuffix(path,"/stop"))){return strings.TrimPrefix(path,"/v1/unified"),true}
 return "",false
}
func unifiedCatalogIDs()map[string]bool{
 raw,e:=os.ReadFile("/opt/harmonika-ai/unified_engine/catalog_source.json");if e!=nil{return nil};var s struct{Skills []struct{ID string `json:"id"`} `json:"skills"`};if json.Unmarshal(raw,&s)!=nil{return nil};out:=map[string]bool{};for _,r:=range s.Skills{out[r.ID]=true};return out
}
func unifiedBillable(path string)bool{return path!="/ticket"&&!strings.HasSuffix(path,"/stop")}
func(a *app)publicUnifiedAPI(w http.ResponseWriter,r *http.Request){
 path,valid:=unifiedTarget(r.Method,r.URL.Path);if !valid{jsonOut(w,404,map[string]any{"error":map[string]any{"code":"not_found"}});return}
 ticketWS:=path=="/ws"&&r.URL.Query().Get("ticket")!=""
 var t publicAIToken
 if !ticketWS{
  var ok bool;t,ok=a.telcoToken(w,r);if !ok{return};scope:=false;for _,s:=range strings.Split(t.Scopes,","){if s=="chat"||s=="skills"||s=="unified"{scope=true}};if !scope{jsonOut(w,403,map[string]any{"error":map[string]any{"code":"unified_scope_required"}});return}
 }
 if r.URL.RawQuery!=""{q:=r.URL.Query();valid:=len(q)==1&&strings.HasSuffix(path,"/events")&&q.Get("after_sequence")!="";if path=="/ws"{valid=len(q)==1&&len(q["ticket"])==1&&regexp.MustCompile(`^[A-Za-z0-9_-]{43}$`).MatchString(q.Get("ticket"))};if !valid{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unsupported_query"}});return};if path!="/ws"{path+="?"+q.Encode()}}
 raw,e:=io.ReadAll(http.MaxBytesReader(w,r.Body,131072));if e!=nil{jsonOut(w,413,map[string]any{"error":map[string]any{"code":"input_too_large"}});return}
 if r.Method=="POST"{if coreUniqueJSON(raw)!=nil{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"invalid_json"}});return};if unifiedBillable(path){if status,code:=a.reservePublicAIQuota(r.Context(),&t,r);status!=0{jsonOut(w,status,map[string]any{"error":map[string]any{"code":code}});return}}}
 secret,e:=os.ReadFile("/etc/harmonika-ai/unified-engine.token");if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"unified_unavailable"}});return}
 if path=="/ws"{
  bearer:=r.Header.Get("Authorization");secretValue:=strings.TrimSpace(string(secret));query:=r.URL.Query().Encode()
  proxy:=httputil.ReverseProxy{Director:func(req *http.Request){
   req.URL.Scheme="http";req.URL.Host="127.0.0.1:18196";req.URL.Path="/ws";req.URL.RawPath="";req.URL.RawQuery=query;req.Host="127.0.0.1:18196"
   for k:=range req.Header{if strings.HasPrefix(strings.ToLower(k),"x-harmonika-"){req.Header.Del(k)}}
   req.Header.Del("Authorization");req.Header.Set("X-Harmonika-Unified-Secret",secretValue)
   if !ticketWS{req.Header.Set("X-Harmonika-Principal",fmt.Sprintf("api-token:%d",t.ID));req.Header.Set("X-Harmonika-Unified-Scopes",t.Scopes);req.Header.Set("X-Harmonika-Delegated-Authorization",bearer)}
  },ErrorHandler:func(w http.ResponseWriter,_ *http.Request,_ error){jsonOut(w,503,map[string]any{"error":map[string]any{"code":"websocket_upstream_unavailable"}})}}
  proxy.ServeHTTP(w,r);return
 }
 ctx,cancel:=context.WithTimeout(r.Context(),35*time.Second);defer cancel();req,e:=http.NewRequestWithContext(ctx,r.Method,"http://127.0.0.1:18196"+path,bytes.NewReader(raw));if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"unified_unavailable"}});return}
 req.Header.Set("X-Harmonika-Unified-Secret",strings.TrimSpace(string(secret)));req.Header.Set("X-Harmonika-Principal",fmt.Sprintf("api-token:%d",t.ID));req.Header.Set("X-Harmonika-Delegated-Authorization",r.Header.Get("Authorization"));req.Header.Set("Content-Type","application/json")
 req.Header.Set("X-Harmonika-Unified-Scopes",t.Scopes);req.Header.Set("Origin",r.Header.Get("Origin"))
 for _,k:=range []string{"Idempotency-Key","Last-Event-ID","Accept"}{req.Header.Set(k,r.Header.Get(k))}
 client:=http.Client{CheckRedirect:func(_ *http.Request,_ []*http.Request)error{return http.ErrUseLastResponse}};res,e:=client.Do(req);if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"unified_unavailable"}});return};defer res.Body.Close();forwardTelcoResponse(w,res)
}
func publicUnifiedReady()bool{
 if _,e:=os.Stat("/var/lib/harmonika-unified/ready.json");e!=nil{return false};c:=http.Client{Timeout:300*time.Millisecond};r,e:=c.Get("http://127.0.0.1:18196/healthz");if e!=nil{return false};defer r.Body.Close();return r.StatusCode==200
}
func publicUnifiedCompletionReady()bool{
 raw,e:=os.ReadFile("/var/lib/harmonika-unified/ready.json");if e!=nil{return false};var marker struct{Completion bool `json:"completion"`};if json.Unmarshal(raw,&marker)!=nil||!marker.Completion{return false};return publicUnifiedReady()
}
