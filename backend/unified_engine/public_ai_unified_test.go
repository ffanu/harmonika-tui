package main
import "testing"
func TestUnifiedExactRoutes(t *testing.T){
 id:="00000000-0000-4000-8000-000000000000"
 for _,p:=range []string{"/v1/unified/capabilities","/v1/unified/skills/core.act","/v1/unified/jobs/"+id+"/events","/v1/unified/sessions/"+id}{if _,ok:=unifiedTarget("GET",p);!ok{t.Fatal(p)}}
 for _,p:=range []string{"/v1/unified/jobs/../../etc/passwd","/v1/unified/jobs/"+id+"/stop","/v1/unified/secrets","/v1/unified/skills/a/b"}{if _,ok:=unifiedTarget("GET",p);ok{t.Fatal("invalid route accepted")}}
 if _,ok:=unifiedTarget("POST","/v1/unified/jobs/"+id);ok{t.Fatal("invalid mutation")}
}
func TestUnifiedControlPlaneQuota(t *testing.T){
 if unifiedBillable("/ticket")||unifiedBillable("/jobs/00000000-0000-4000-8000-000000000000/stop"){t.Fatal("read/control must work after generation quota is spent")}
 if !unifiedBillable("/jobs")||!unifiedBillable("/bus/events"){t.Fatal("generation/write quota bypass")}
}
