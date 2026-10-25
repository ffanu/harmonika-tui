package main
import("io";"net/http";"net/http/httptest";"strings";"testing")
func TestLegalScopeRoutesAndMethods(t *testing.T){
 for _,p:=range []string{"/v1/legal/capabilities","/v1/legal/cases","/v1/legal/files/00000000-0000-0000-0000-000000000000/download","/v1/legal/requests/00000000-0000-0000-0000-000000000000/events"}{if !legalPath.MatchString(p){t.Fatal(p)}}
 for _,p:=range []string{"/v1/legal/admin","/v1/legal/../../etc/passwd","/v1/legal/connectors","/v1/legal/files/1/download"}{if legalPath.MatchString(p){t.Fatal(p)}}
 if legalScopeAllowed(publicAIToken{Scopes:"models"})||!legalScopeAllowed(publicAIToken{Scopes:"legal"})||!legalScopeAllowed(publicAIToken{Scopes:"chat"}){t.Fatal("scope boundary")}
 if legalMethodAllowed("GET","/v1/legal/invoke")||legalMethodAllowed("POST","/v1/legal/capabilities"){t.Fatal("invalid method allowed")}
}
func TestLegalChatRejectsUntrustedContext(t *testing.T){
 for _,in:=range []publicAIChatCompletionRequest{{Legal:true,Telco:true},{Legal:true,WorkspaceID:"anything"},{Legal:true}}{
  w:=httptest.NewRecorder();(&app{}).publicLegalChat(w,publicDocsRequest("POST","/v1/chat/completions",""),publicAIToken{Scopes:"chat"},in);if w.Code!=400{t.Fatal(w.Code)}
 }
}
func TestLegalOpenAIStreamKeepsProgressAndRealFinal(t *testing.T){
 raw:="data: {\"type\":\"response.created\",\"data\":{\"status\":\"checking_sources\"}}\n\ndata: {\"type\":\"response.completed\",\"data\":{\"status\":\"completed\",\"result\":{\"text\":\"Grounded fixture only\"}}}\n\ndata: [DONE]\n\n"
 w:=httptest.NewRecorder();forwardLegalChatSSE(w,&http.Response{Body:io.NopCloser(strings.NewReader(raw))});if !strings.Contains(w.Body.String(),"Grounded fixture only")||!strings.Contains(w.Body.String(),"chat.completion.chunk")||!strings.HasSuffix(w.Body.String(),"data: [DONE]\n\n"){t.Fatal(w.Body.String())}
}
func TestLegalStreamPreservesTerminalError(t *testing.T){
 raw:="data: {\"type\":\"response.error\",\"data\":{\"code\":\"rate_limited\"}}\n\ndata: [DONE]\n\n";w:=httptest.NewRecorder();forwardLegalChatSSE(w,&http.Response{Body:io.NopCloser(strings.NewReader(raw))});text:=w.Body.String();if !strings.Contains(text,"rate_limited")||strings.Contains(text,"legal_stream_interrupted"){t.Fatal(text)}
}
