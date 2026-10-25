package main
import("strings";"testing";"encoding/json")
func TestCoreUniqueJSON(t *testing.T){
 for _,raw:=range []string{`{"a":1,"a":2}`,`{"x":{"a":1,"a":2}}`,`{} {}`,strings.Repeat("[",25)+"0"+strings.Repeat("]",25)}{if coreUniqueJSON([]byte(raw))==nil{t.Fatal("unsafe JSON accepted")}}
 if e:=coreUniqueJSON([]byte(`{"a":[1,{"b":false}],"x":"y"}`));e!=nil{t.Fatal(e)}
}
func TestCoreDelegationRequiresWorkspace(t *testing.T){
 v:=masterInvocation{SkillID:"file.read",Input:map[string]any{"path":"a"}}
 if _,_,e:=coreDelegation(v,"fs.read");e==nil{t.Fatal("missing workspace")}
 v.Context.WorkspaceID="00000000-0000-4000-8000-000000000000";path,body,e:=coreDelegation(v,"fs.read");if e!=nil||path!="/v1/coding/workspaces/00000000-0000-4000-8000-000000000000/invoke"||body["skill_id"]!="fs.read"{t.Fatal("wrong delegation")}
 v.Context.Engine="legal";if _,_,e:=coreDelegation(v,"fs.read");e==nil{t.Fatal("engine spoof")}
}
func TestCoreSystemInfoHasNoCallerCode(t *testing.T){
 v:=masterInvocation{SkillID:"system.info",Input:map[string]any{},Context:masterContext{WorkspaceID:"00000000-0000-4000-8000-000000000000"}}
 _,body,e:=coreDelegation(v,"exec.python");if e!=nil{t.Fatal(e)};input:=body["input"].(map[string]any);if !strings.Contains(input["code"].(string),"owned_sandbox"){t.Fatal("missing scope")}
 v.Input["command"]="bad";if _,_,e:=coreDelegation(v,"exec.python");e==nil{t.Fatal("caller code injection")}
}
func TestCoreChatGuard(t *testing.T){
 v:=masterInvocation{SkillID:"llm.complete",Input:map[string]any{"prompt":"reply OK"}}
 raw,e:=coreChatBody(v);if e!=nil||!strings.Contains(string(raw),"harmonika-ai"){t.Fatal("chat adapter")}
 v.Input["tools"]=[]any{};if _,e:=coreChatBody(v);e==nil{t.Fatal("tools injection")}
 v=masterInvocation{SkillID:"llm.chat",Input:map[string]any{"messages":[]any{map[string]any{"role":"tool","content":"bad"}}}}
 if _,e:=coreChatBody(v);e==nil{t.Fatal("role injection")}
}

func TestCoreIntegerPrecision(t *testing.T){
 for _,number:=range []string{"9007199254740993","9007199254740992"}{
  raw:=[]byte(`{"skill_id":"memory.set","input":{"key":"x","value":`+number+`}}`)
  v,e:=masterDecode(raw);if e!=nil{t.Fatal(e)};encoded,_:=json.Marshal(v.Input)
  if !strings.Contains(string(encoded),number){t.Fatal("integer precision lost")}
 }
}
