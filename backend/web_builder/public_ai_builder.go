package main

import (
 "bytes"
 "context"
 "fmt"
 "io"
 "net/http"
 "os"
 "regexp"
 "strings"
 "time"
)
var builderPath=regexp.MustCompile(`^/v1/builder/(capabilities|projects(/[0-9a-f-]{36}(/invoke|/files|/artifact|/build|/deploy)?)?|jobs/[0-9a-f-]{36}(/events|/approve|/stop|/deployment)?)$`)
func builderMethodAllowed(method,path string)bool{
 if method=="GET"{return path=="/v1/builder/capabilities"||path=="/v1/builder/projects"||regexp.MustCompile(`^/v1/builder/projects/[0-9a-f-]{36}(/artifact)?$`).MatchString(path)||regexp.MustCompile(`^/v1/builder/jobs/[0-9a-f-]{36}(/events|/deployment)?$`).MatchString(path)}
 if method=="POST"{return path=="/v1/builder/projects"||strings.HasSuffix(path,"/invoke")||strings.HasSuffix(path,"/build")||strings.HasSuffix(path,"/deploy")||strings.HasSuffix(path,"/files")||strings.HasSuffix(path,"/approve")||strings.HasSuffix(path,"/stop")};return false
}
func builderUpstream(ctx context.Context,tok publicAIToken,method,path string,body []byte,h http.Header)(*http.Response,error){
 secret,e:=os.ReadFile("/etc/harmonika-ai/builder-engine.token");if e!=nil{return nil,fmt.Errorf("builder_unavailable")}
 r,e:=http.NewRequestWithContext(ctx,method,"http://127.0.0.1:18191"+path,bytes.NewReader(body));if e!=nil{return nil,e};r.Header.Set("X-Harmonika-Builder-Secret",strings.TrimSpace(string(secret)));r.Header.Set("X-Harmonika-Principal",fmt.Sprintf("api-token:%d",tok.ID))
 for _,k:=range []string{"Content-Type","Accept","Idempotency-Key","Last-Event-ID"}{if v:=h.Get(k);v!=""{r.Header.Set(k,v)}}
 return (&http.Client{CheckRedirect:func(_ *http.Request,_ []*http.Request)error{return http.ErrUseLastResponse}}).Do(r)
}
func(a *app)publicBuilderAPI(w http.ResponseWriter,r *http.Request){
 if !builderPath.MatchString(r.URL.Path){jsonOut(w,404,map[string]any{"error":map[string]any{"code":"not_found"}});return}
 tok,ok:=a.telcoToken(w,r);if !ok{return};scope:=false;for _,v:=range strings.Split(tok.Scopes,","){if v=="chat"||v=="builder"||v=="coding"{scope=true}};if !scope{jsonOut(w,403,map[string]any{"error":map[string]any{"code":"builder_scope_required"}});return}
 if !builderMethodAllowed(r.Method,r.URL.Path){jsonOut(w,405,map[string]any{"error":map[string]any{"code":"method_not_allowed"}});return}
 if r.URL.RawQuery!=""{q:=r.URL.Query();if !strings.HasSuffix(r.URL.Path,"/events")||len(q)!=1||q.Get("after_sequence")==""{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unsupported_query"}});return}}
 raw,e:=io.ReadAll(http.MaxBytesReader(w,r.Body,1048576));if e!=nil{jsonOut(w,413,map[string]any{"error":map[string]any{"code":"input_too_large"}});return}
 if r.Method=="POST"{if status,code:=a.reservePublicAIQuota(r.Context(),&tok,r);status!=0{jsonOut(w,status,map[string]any{"error":map[string]any{"code":code}});return}}
 path:=strings.TrimPrefix(r.URL.Path,"/v1/builder");if r.URL.RawQuery!=""{path+="?"+r.URL.Query().Encode()}
 ctx,cancel:=context.WithTimeout(r.Context(),185*time.Second);defer cancel();res,e:=builderUpstream(ctx,tok,r.Method,path,raw,r.Header);if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"builder_unavailable"}});return};defer res.Body.Close()
 if strings.HasSuffix(r.URL.Path,"/artifact")&&res.StatusCode==200{
  if res.ContentLength<0||res.ContentLength>24*1024*1024{jsonOut(w,502,map[string]any{"error":map[string]any{"code":"invalid_artifact_size"}});return};w.Header().Set("Content-Type","application/zip");w.Header().Set("Content-Disposition","attachment; filename=harmonika-project.zip");w.Header().Set("Cache-Control","no-store");w.Header().Set("X-Content-Type-Options","nosniff");w.Header().Set("Content-Length",fmt.Sprint(res.ContentLength));io.Copy(w,io.LimitReader(res.Body,res.ContentLength));return}
 forwardTelcoResponse(w,res)
}
func publicBuilderReady() bool {
 if _,e:=os.Stat("/var/lib/harmonika-builder/ready.json");e!=nil{return false}
 c:=http.Client{Timeout:300*time.Millisecond};r,e:=c.Get("http://127.0.0.1:18191/healthz");if e!=nil{return false};defer r.Body.Close();return r.StatusCode==200
}
