package main

import (
 "bufio"
 "bytes"
 "context"
 "encoding/json"
 "fmt"
 "io"
 "net/http"
 "os"
 "regexp"
 "strconv"
 "strings"
 "time"
)
var legalPath=regexp.MustCompile(`^/v1/legal/(capabilities|invoke|chat|sources(/refresh)?|cases(/[0-9a-f-]{36})?|files(/[0-9a-f-]{36}/download)?|requests/[0-9a-f-]{36}(/events)?)$`)
func publicLegalReady()bool{if _,e:=os.Stat("/var/lib/harmonika-legal/ready.json");e!=nil{return false};c:=http.Client{Timeout:300*time.Millisecond};r,e:=c.Get("http://127.0.0.1:18190/healthz");if e!=nil{return false};r.Body.Close();return r.StatusCode==200}
func legalScopeAllowed(tok publicAIToken)bool{for _,v:=range strings.Split(tok.Scopes,","){if v=="chat"||v=="legal"{return true}};return false}
func legalUpstream(ctx context.Context,tok publicAIToken,method,path string,body []byte,h http.Header)(*http.Response,error){
 secret,e:=os.ReadFile("/etc/harmonika-ai/legal-engine.token");if e!=nil{return nil,fmt.Errorf("legal_unavailable")};r,e:=http.NewRequestWithContext(ctx,method,"http://127.0.0.1:18190"+path,bytes.NewReader(body));if e!=nil{return nil,e}
 r.Header.Set("X-Harmonika-Legal-Secret",strings.TrimSpace(string(secret)));if tok.Tier=="internal"{r.Header.Set("X-Harmonika-Legal-Observer","1")};r.Header.Set("X-Harmonika-Principal",fmt.Sprintf("api-token:%d",tok.ID));for _,k:=range []string{"Content-Type","Accept","Idempotency-Key","Last-Event-ID"}{if v:=h.Get(k);v!=""{r.Header.Set(k,v)}}
 client:=http.Client{CheckRedirect:func(_ *http.Request,_ []*http.Request)error{return http.ErrUseLastResponse}};return client.Do(r)
}
func legalMethodAllowed(method,path string)bool{
 if method=="POST"{return path=="/v1/legal/cases"||path=="/v1/legal/files"||path=="/v1/legal/invoke"||path=="/v1/legal/chat"||path=="/v1/legal/sources/refresh"}
 if method=="GET"{return path=="/v1/legal/capabilities"||path=="/v1/legal/sources"||strings.HasPrefix(path,"/v1/legal/cases/")||strings.HasPrefix(path,"/v1/legal/files/")||strings.HasPrefix(path,"/v1/legal/requests/")};return false
}
func legalChatText(meta map[string]any)string{result,_:=meta["result"].(map[string]any);text,_:=result["text"].(string);if text!=""{return text};return "Permintaan belum dapat diproses dengan data yang tersedia. Periksa status request dan kelengkapan perkara. Edukasi hukum, bukan nasihat hukum formal atau pengganti advokat."}
func(a *app)publicLegalAPI(w http.ResponseWriter,r *http.Request){
 if !legalPath.MatchString(r.URL.Path){jsonOut(w,404,map[string]any{"error":map[string]any{"code":"not_found"}});return}
 tok,ok:=a.telcoToken(w,r);if !ok{return};if !legalScopeAllowed(tok){jsonOut(w,403,map[string]any{"error":map[string]any{"code":"legal_scope_required"}});return}
 if !legalMethodAllowed(r.Method,r.URL.Path){jsonOut(w,405,map[string]any{"error":map[string]any{"code":"method_not_allowed"}});return}
 limit:=int64(262144);if r.Method=="POST"&&r.URL.Path=="/v1/legal/files"{limit=10*1024*1024+65536}
 raw,e:=io.ReadAll(http.MaxBytesReader(w,r.Body,limit));if e!=nil{jsonOut(w,413,map[string]any{"error":map[string]any{"code":"input_too_large"}});return}
 if r.Method=="POST"{if status,code:=a.reservePublicAIQuota(r.Context(),&tok,r);status!=0{jsonOut(w,status,map[string]any{"error":map[string]any{"code":code}});return}}
 path:=strings.TrimPrefix(r.URL.Path,"/v1/legal");if r.URL.RawQuery!=""{v:=r.URL.Query();if len(v)!=1||v.Get("after_sequence")==""{jsonOut(w,400,map[string]any{"error":map[string]any{"code":"unsupported_query"}});return};path+="?"+v.Encode()}
 ctx,cancel:=context.WithTimeout(r.Context(),100*time.Second);defer cancel();res,e:=legalUpstream(ctx,tok,r.Method,path,raw,r.Header);if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"legal_unavailable"}});return};defer res.Body.Close()
 if strings.HasSuffix(r.URL.Path,"/download")&&res.StatusCode==200{
  if res.ContentLength<0||res.ContentLength>10*1024*1024{jsonOut(w,502,map[string]any{"error":map[string]any{"code":"invalid_evidence_length"}});return};w.Header().Set("Content-Length",strconv.FormatInt(res.ContentLength,10));w.Header().Set("Content-Type",res.Header.Get("Content-Type"));w.Header().Set("Content-Disposition",res.Header.Get("Content-Disposition"));w.Header().Set("Cache-Control","no-store");w.Header().Set("X-Content-Type-Options","nosniff");io.Copy(w,io.LimitReader(res.Body,res.ContentLength));return}
 forwardTelcoResponse(w,res)
}
func(a *app)publicLegalChat(w http.ResponseWriter,r *http.Request,tok publicAIToken,in publicAIChatCompletionRequest){
 if !legalScopeAllowed(tok){jsonOut(w,403,map[string]any{"error":map[string]any{"code":"legal_scope_required"}});return}
 if in.Telco||in.WorkspaceID!=""||len(in.Tools)>0||in.ToolChoice!=nil||publicAIHasToolHistory(in){jsonOut(w,400,map[string]any{"error":map[string]any{"code":"conflicting_engine_context"}});return}
 if !regexp.MustCompile(`^[0-9a-f-]{36}$`).MatchString(in.LegalCaseID){jsonOut(w,400,map[string]any{"error":map[string]any{"code":"legal_case_required"}});return}
 text:="";for _,m:=range in.Messages{if m.Role=="user"{text=publicAIContentText(m.Content)}}
 body,_:=json.Marshal(map[string]any{"case_id":in.LegalCaseID,"message":text,"stream":in.Stream});headers:=r.Header.Clone();headers.Set("Content-Type","application/json")
 ctx,cancel:=context.WithTimeout(r.Context(),100*time.Second);defer cancel();res,e:=legalUpstream(ctx,tok,"POST","/chat",body,headers);if e!=nil{jsonOut(w,503,map[string]any{"error":map[string]any{"code":"legal_unavailable"}});return};defer res.Body.Close()
 if res.StatusCode!=200{forwardTelcoResponse(w,res);return};if in.Stream{forwardLegalChatSSE(w,res);return}
 raw,_:=io.ReadAll(io.LimitReader(res.Body,1048577));v:=map[string]any{};if len(raw)>1048576||json.Unmarshal(raw,&v)!=nil{jsonOut(w,502,map[string]any{"error":map[string]any{"code":"invalid_legal_response"}});return};content:=legalChatText(v)
 jsonOut(w,200,map[string]any{"id":v["id"],"object":"chat.completion","model":"harmonika-ai","choices":[]any{map[string]any{"index":0,"message":map[string]any{"role":"assistant","content":content},"finish_reason":"stop"}},"harmonika":v})
}

func forwardLegalChatSSE(w http.ResponseWriter,res *http.Response){
 w.Header().Set("Content-Type","text/event-stream; charset=utf-8");w.Header().Set("Cache-Control","no-store");w.Header().Set("X-Accel-Buffering","no");f,_:=w.(http.Flusher)
 id:=fmt.Sprintf("legal-%d",time.Now().UnixNano());send:=func(delta map[string]any,reason any,meta any){v:=map[string]any{"id":id,"object":"chat.completion.chunk","model":"harmonika-ai","choices":[]any{map[string]any{"index":0,"delta":delta,"finish_reason":reason}},"harmonika":meta};raw,_:=json.Marshal(v);fmt.Fprintf(w,"data: %s\n\n",raw);if f!=nil{f.Flush()}}
 send(map[string]any{"role":"assistant"},nil,nil);scanner:=bufio.NewScanner(res.Body);scanner.Buffer(make([]byte,8192),1048576);done:=false;total:=0
 for scanner.Scan(){line:=scanner.Text();total+=len(line);if total>4*1024*1024{break};if strings.HasPrefix(line,":"){fmt.Fprint(w,": harmonika checking sources\n\n");if f!=nil{f.Flush()};continue};if !strings.HasPrefix(line,"data: "){continue};raw:=strings.TrimPrefix(line,"data: ");if raw=="[DONE]"{break};v:=map[string]any{};if json.Unmarshal([]byte(raw),&v)!=nil{continue};if v["type"]=="response.completed"{meta,_:=v["data"].(map[string]any);text:=legalChatText(meta);send(map[string]any{"content":text},"stop",meta);done=true}else if v["type"]=="response.error"{send(map[string]any{},"stop",v);done=true}else{send(map[string]any{},nil,v)}}
 if !done{send(map[string]any{},"stop",map[string]any{"status":"interrupted","error":map[string]any{"code":"legal_stream_interrupted"}})};fmt.Fprint(w,"data: [DONE]\n\n");if f!=nil{f.Flush()}
}
