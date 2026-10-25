package main
import("testing";"net/http")
func TestMobileExactNamespace(t *testing.T){
 for _,row:=range []struct{method,path,target string}{
  {"GET","/v1/mobile-builder/capabilities","/mobile/capabilities"},
  {"GET","/v1/mobile-builder/skills","/mobile/skills"},
  {"GET","/v1/mobile-builder/skills/spec.read.mobile","/mobile/skills/spec.read.mobile"},
  {"POST","/v1/mobile-builder/invoke","/mobile/invoke"},
 }{v,ok:=mobileTarget(row.method,row.path);if !ok||v!=row.target{t.Fatal(row.path)}}
 for _,path:=range []string{"/v1/mobile-builder/skills/../invoke","/v1/mobile-builder/invoke/extra","/v1/mobile-builder/skills/a/b","/v1/mobile-builder/skills/UPPER","/v1/mobile-builder/skills/"}{if _,ok:=mobileTarget(http.MethodGet,path);ok{t.Fatal(path)}}
 if _,ok:=mobileTarget("DELETE","/v1/mobile-builder/invoke");ok{t.Fatal("method confusion")}
}
