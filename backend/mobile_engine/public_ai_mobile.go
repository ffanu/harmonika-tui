package main

import (
 "context"
 "io"
 "os"
 "net/http"
 "strings"
 "time"
)

// A separate exact namespace prevents a source generator from invoking host tooling.
func mobileTarget(method,path string)(string,bool){
 if method==http.MethodGet {
  switch {
  case path=="/v1/mobile-builder/capabilities":return "/mobile/capabilities",true
  case path=="/v1/mobile-builder/skills":return "/mobile/skills",true
  case strings.HasPrefix(path,"/v1/mobile-builder/skills/"):
   id:=strings.TrimPrefix(path,"/v1/mobile-builder/skills/")
   if len(id)<=100 && masterSkillID.MatchString(id){return "/mobile/skills/"+id,true}
  }
 }
 if method==http.MethodPost && path=="/v1/mobile-builder/invoke"{return "/mobile/invoke",true}
 return "",false
}
func(a *app)publicMobileBuilder(w http.ResponseWriter,r *http.Request){
 target,valid:=mobileTarget(r.Method,r.URL.Path)
 if !valid{jsonOut(w,404,map[string]any{"error":map[string]any{"code":"not_found"}});return}
 t,ok:=a.telcoToken(w,r);if !ok{return}
 allowed:=false
 for _,scope:=range strings.Split(t.Scopes,","){if scope=="chat"||scope=="skills"||scope=="mobile"{allowed=true}}
 if !allowed{jsonOut(w,403,map[string]any{"error":map[string]any{"code":"mobile_scope_required"}});return}
 raw,e:=io.ReadAll(http.MaxBytesReader(w,r.Body,65536));if e!=nil{jsonOut(w,413,map[string]any{"error":map[string]any{"code":"input_too_large"}});return}
 if r.Method==http.MethodPost{
  if r.URL.RawQuery!=""{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unsupported_query"}});return}
  v,e:=masterDecode(raw)
  if e!=nil||v.Context!=(masterContext{}){jsonOut(w,400,map[string]any{"error":map[string]any{"code":"invalid_request"}});return}
  if status,code:=a.reservePublicAIQuota(r.Context(),&t,r);status!=0{jsonOut(w,status,map[string]any{"error":map[string]any{"code":code}});return}
 }
 if r.URL.RawQuery!=""{
  if target!="/mobile/skills"{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unsupported_query"}});return}
  target+="?"+r.URL.Query().Encode()
 }
 ctx,cancel:=context.WithTimeout(r.Context(),20*time.Second);defer cancel()
 res,e:=masterUpstream(ctx,t,r.Method,target,raw)
 if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"mobile_builder_unavailable"}});return}
 defer res.Body.Close();forwardTelcoResponse(w,res)
}

func publicMobileReady()bool{
 if _,e:=os.Stat("/var/lib/harmonika-master/mobile-ready.json");e!=nil{return false}
 return publicMasterReady()
}
