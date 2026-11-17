'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const agent=require('../backend/planner/agent-controller.cjs');
const result=()=>({ok:true,skill_id:'operations.skills.selftest',result:{status:'passed',execution_scope:'isolated_synthetic_fixture',test_count:2,passed:2,failed:0,checks:[{check:'one',passed:true},{check:'two',passed:true}]}});
const history=body=>({messages:[{role:'user',content:'Jalankan tes selftest engine Harmonika.'},{role:'assistant',tool_calls:[{id:'c1',function:{name:'engine_operations',arguments:JSON.stringify({skill_id:'operations.skills.selftest',input:{suite:'core'}})}}]},{role:'tool',tool_call_id:'c1',content:JSON.stringify(body)}]});
const complete=()=>({content:'Tes lulus: 2 tes fixture engine.',tool_calls:[],task_state:{phase:'complete',remaining:[],next_step:'',evidence_call_ids:['c1']}});
test('actual-count isolated operations receipt can finish selftest without invalid_tool_response',()=>{assert.doesNotThrow(()=>agent.check(complete(),history(result())))});
test('missing counts, failed check, zero tests or mismatched operation never prove tests passed',()=>{
 for(const mutate of [x=>delete x.result.checks,x=>x.result.checks[0].passed=false,x=>x.result.test_count=0,x=>x.skill_id='operations.browser.regression',x=>x.result.execution_scope='user_claim']){
  const body=result();mutate(body);assert.throws(()=>agent.check(complete(),history(body)),e=>e.decisionReason==='unverified_completion');
 }
});
test('isolated engine checks cannot certify edits in a user project',()=>{
 const data=history(result());data.messages[0].content='Perbaiki kode aplikasi dan jalankan tes.';
 data.messages.splice(1,0,{role:'assistant',tool_calls:[{id:'edit',function:{name:'fixture.edit',arguments:JSON.stringify({path:'/tmp/project/app.js'})}}]},{role:'tool',tool_call_id:'edit',content:JSON.stringify({ok:true})});
 assert.throws(()=>agent.check(complete(),data),e=>e.decisionReason==='unverified_completion');
});
test('unsigned selftest counts cannot substitute for registered operator proof',()=>{assert.throws(()=>agent.check(complete(),history(result()),()=>({required:['tests'],checks:[]})),e=>e.decisionReason==='unverified_completion')});
