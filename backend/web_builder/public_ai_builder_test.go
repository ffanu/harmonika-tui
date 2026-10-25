package main
import("net/http";"net/http/httptest";"testing")
func TestBuilderBoundaryPaths(t *testing.T){
 for _,p:=range []string{"/v1/builder/capabilities","/v1/builder/projects","/v1/builder/projects/00000000-0000-0000-0000-000000000000/invoke","/v1/builder/jobs/00000000-0000-0000-0000-000000000000/events"}{if !builderPath.MatchString(p){t.Fatal("missing path",p)}}
 for _,p:=range []string{"/v1/builder/_internal","/v1/builder/../secret","/v1/builder/projects/file:///etc/passwd","/v1/builder/targets"}{if builderPath.MatchString(p){t.Fatal("unsafe path",p)}}
 if builderMethodAllowed("GET","/v1/builder/projects/00000000-0000-0000-0000-000000000000/invoke")||builderMethodAllowed("DELETE","/v1/builder/projects"){t.Fatal("invalid method")}
 r:=httptest.NewRequest(http.MethodGet,"/v1/builder/_internal",nil);w:=httptest.NewRecorder();(&app{}).publicBuilderAPI(w,r);if w.Code!=404{t.Fatal(w.Code)}
}
