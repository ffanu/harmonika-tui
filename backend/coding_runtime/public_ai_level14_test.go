package main

import (
 "context"
 "io"
 "net/http"
 "os"
 "strings"
 "testing"
)
type level14RoundTrip func(*http.Request)(*http.Response,error)
func(f level14RoundTrip) RoundTrip(r *http.Request)(*http.Response,error){return f(r)}
func TestLevel14VerifiedPrincipalOverridesCallerHeaders(t *testing.T){
 if _,err:=os.Stat("/etc/harmonika-ai/coding-runtime.token");err!=nil{t.Skip("private integration credential unavailable")}
 previous:=http.DefaultTransport
 defer func(){http.DefaultTransport=previous}()
 called:=false
 http.DefaultTransport=level14RoundTrip(func(r *http.Request)(*http.Response,error){
  called=true
  if r.Header.Get("X-Harmonika-Principal")!="api-token:37"{t.Fatal("principal was not derived from verified token")}
  secret:=r.Header.Get("X-Harmonika-Coding-Secret")
  if secret==""||secret=="caller-forged"{t.Fatal("bridge credential was not server-derived")}
  if r.Header.Get("Idempotency-Key")!="level14-fixed-key"{t.Fatal("idempotency key lost")}
  b,err:=io.ReadAll(r.Body);if err!=nil{t.Fatal("request body read failed")}
  if !strings.Contains(string(b),`"profile":"tests"`){t.Fatal("verification policy was not forwarded")}
  return &http.Response{StatusCode:200,Body:io.NopCloser(strings.NewReader(`{"ok":true}`)),Header:make(http.Header)},nil
 })
 headers:=http.Header{"X-Harmonika-Principal":[]string{"api-token:999"},"X-Harmonika-Coding-Secret":[]string{"caller-forged"},"Idempotency-Key":[]string{"level14-fixed-key"}}
 response,err:=codingUpstream(context.Background(),publicAIToken{ID:37},"POST","/workspaces/synthetic/agent",[]byte(`{"verification":{"profile":"tests"}}`),headers)
 if err!=nil{t.Fatal("coding bridge request failed")};response.Body.Close();if !called{t.Fatal("bridge transport was not reached")}
}
