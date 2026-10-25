package main
import("testing";"strings")
func TestMasterStrictDecode(t *testing.T){
 for _,raw:=range []string{`{"skill_id":"code.read","input":{},"actor":"root"}`,`{"skill_id":"x","input":{},"context":{"actor":"root"}}`,`{"skill_id":"x","input":{}} {}`,`{"skill_id":"../../secret","input":{}}`,`{"skill_id":"x","input":null}`} {if _,e:=masterDecode([]byte(raw));e==nil{t.Fatal("accepted unknown context",raw)}}
 if _,e:=masterDecode([]byte(`{"skill_id":"static.ast.parse","input":{"code":"x=1"}}`));e!=nil{t.Fatal(e)}
}
func TestMasterContextRouting(t *testing.T){
 u:="01234567-1234-4234-8234-123456789abc";v:=masterInvocation{SkillID:"scaffold.fastapi",Input:map[string]any{"project":"demo"},Context:masterContext{ProjectID:u}}
 target,body,e:=masterEngineRoute(v,"builder");if e!=nil||!strings.Contains(target,u)||body["skill_id"]!=v.SkillID{t.Fatal("builder mapping")}
 v.Context.ProjectID="../../bad";if _,_,e=masterEngineRoute(v,"builder");e==nil{t.Fatal("invalid project")}
 v.Context=masterContext{Engine:"legal"};if _,_,e=masterEngineRoute(v,"builder");e==nil{t.Fatal("engine override")}
 v.Context=masterContext{};target,_,e=masterEngineRoute(v,"legal");if e!=nil||target!="/v1/legal/invoke"{t.Fatal("legal route")}
 v.Context=masterContext{WorkspaceID:u};target,_,e=masterEngineRoute(v,"coding");if e!=nil||target!="/v1/coding/workspaces/"+u+"/invoke"{t.Fatal("coding route")}
}

func TestMasterDomainAmbiguity(t *testing.T){for _,id:=range []string{"evidence.chain.custody","evidence.store.secure","debug.attach","debug.breakpoint","test.run","test.single","test.coverage"}{v:=masterInvocation{SkillID:id,Input:map[string]any{},Context:masterContext{}};_,_,e:=masterEngineRoute(v,"coding");if e==nil||e.Error()!="explicit_engine_context_required"{t.Fatal("implicit cross-domain alias",id)}}}
