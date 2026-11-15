package main
import "testing"
func TestLevel12ToolPolicyEnums(t *testing.T) {
 cases:=[]struct{code string;status int;known bool}{
  {"tool_secret_reference_required",400,true},{"invalid_tool_history",400,true},
  {"tool_input_complexity",400,true},{"context_length_exceeded",413,true},
  {"tool_input_too_large",413,true},{"tool_engine_busy",0,false},
  {"unknown_private_error",0,false},{"raw EXAM-SENTINEL",0,false},{"",0,false},
 }
 for _,c:=range cases{status,known:=publicToolPolicyStatus(c.code);if status!=c.status||known!=c.known{t.Fatalf("policy enum mapping failed")}}
}
