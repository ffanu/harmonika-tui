'use strict';
const {test}=require('node:test'),a=require('node:assert/strict');
const lifecycle=require('../backend/planner/job-lifecycle.cjs');
const controller=require('../backend/planner/agent-controller.cjs');
const call=id=>({role:'assistant',tool_calls:[{id,function:{name:'opaque',arguments:'{}'}}]});
const receipt=(id,value)=>({role:'tool',tool_call_id:id,content:JSON.stringify(value)});
const data=value=>({messages:[{role:'user',content:'Finish the synthetic job and verify the artifact.'},call('job'),receipt('job',value)]});
const complete=ids=>({tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:ids,next_step:''}});
for(const status of ['accepted','submitted','started','queued','pending','running','processing','in_progress','scheduled','retrying','cancel_requested','cancelling','canceling','unknown','outcome_unknown','reconciling','busy'])test('nonterminal '+status+' cannot establish completion',()=>{
  const d=data({job_id:'fixture',status});
  a.equal(controller.observe(d).last.pending,true);
  a.throws(()=>controller.check(complete(['job']),d),e=>e.decisionReason==='unverified_completion');
  const waiting={tool_calls:[],task_state:{phase:'waiting',remaining:['artifact'],evidence_call_ids:['job'],next_step:'Inspect the existing job status.'}};
  a.doesNotThrow(()=>controller.check(waiting,d));
});
for(const status of ['cancelled','canceled','timeout','timed_out','expired','aborted','rejected','failure','errored','killed','queue_full','rate_limited'])test('terminal unsuccessful '+status+' is not success',()=>{
  const d=data({status});a.equal(controller.observe(d).last.failed,true);
  a.throws(()=>controller.check(complete(['job']),d),e=>e.decisionReason==='unverified_completion');
});
test('later successful artifact can finish without citing old pending/failed results',()=>{
  const d=data({status:'running'});d.messages.push(call('timeout'),receipt('timeout',{status:'timeout'}),call('done'),receipt('done',{status:'completed',artifact:'fixture.json'}));
  a.doesNotThrow(()=>controller.check(complete(['done']),d));
  for(const id of ['job','timeout'])a.throws(()=>controller.check(complete([id,'done']),d),e=>e.decisionReason==='unverified_completion');
});
test('nonterminal current status cannot be hidden behind an earlier read receipt',()=>{
  const d=data({ok:true});d.messages.push(call('pending'),receipt('pending',{status:'running'}));
  a.throws(()=>controller.check(complete(['job']),d),e=>e.decisionReason==='unverified_completion');
});
test('completion needs cited proof after the latest failed or pending receipt',()=>{
  for(const bad of [{exit_code:1},{status:'running'}]){
    const d=data({ok:true});d.messages.push(call('bad'),receipt('bad',bad),call('read'),receipt('read',{ok:true}));
    a.throws(()=>controller.check(complete(['job']),d),e=>e.decisionReason==='unverified_completion');
    d.messages.push(call('recheck'),receipt('recheck',{exit_code:0,passed:3}));
    a.doesNotThrow(()=>controller.check(complete(['recheck']),d));
  }
});
test('explicit executor metadata and normalized state fields are inspected',()=>{
  a.deepEqual(lifecycle.classify({output:'fixture',metadata:{state:' RUNNING '}}),{pending:true,failed:false});
  a.deepEqual(lifecycle.classify({content:[],metadata:{metadata:{status:'TIMED_OUT'}}}),{pending:false,failed:true});
});
test('do not recursively classify arbitrary data, source text or log words',()=>{
  for(const value of ['The fixture log says running and timeout.',{data:{status:'running'}},{output:'sample status: timeout',metadata:{exit:0}},{status:'completed',result:{state:'running'}},[{status:'timeout'}]])a.deepEqual(lifecycle.classify(value),{pending:false,failed:false});
});
test('status-only conversational report remains possible without claiming completion',()=>{
  a.doesNotThrow(()=>controller.check({tool_calls:[],task_state:{phase:'answer',remaining:[],evidence_call_ids:['job'],next_step:''}},data({status:'running'})));
});
test('guidance reconciles uncertain submissions and preserves privacy',()=>{
  const guide=controller.instructions(data({status:'running',private_fixture_value:'do_not_copy_me'}));
  a.match(guide,/reconcile the existing request\/job/);a.match(guide,/Never create another request ID/);
  a.ok(!guide.includes('do_not_copy_me'));
});
test('cancellation and expired leases require reconciliation rather than assumed physical termination',()=>{
  const guide=lifecycle.instructions();
  a.match(guide,/cancel_requested/);a.match(guide,/lease/);a.match(guide,/Last-Event-ID/);
  a.match(guide,/backpressure/);a.match(guide,/physical/);
});
