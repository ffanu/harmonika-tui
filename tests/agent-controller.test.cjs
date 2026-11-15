'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const agent=require('../backend/planner/agent-controller.cjs');
const recovery=require('../backend/planner/decision-recovery.cjs');
const call=(id,name='opaque',args={path:'fixture.txt'})=>({role:'assistant',content:null,tool_calls:[{id,type:'function',function:{name,arguments:JSON.stringify(args)}}]});
const result=(id,content)=>({role:'tool',tool_call_id:id,content});
const state=(phase,extra={})=>({phase,remaining:[],evidence_call_ids:[],next_step:'',...extra});
const value=(s,calls=[])=>({content:'synthetic response',tool_calls:calls,task_state:s});
const history=()=>({messages:[{role:'user',content:'Fix this fixture.'},call('one'),result('one','{"ok":true}')]});
test('schema requires an internal task contract without modifying caller function schemas',()=>{
  const schema={required:['content','tool_calls'],properties:{content:{type:'string'},tool_calls:{type:'array'}}};
  const before=JSON.stringify(schema),next=agent.extendSchema(schema);
  assert.ok(next.required.includes('task_state'));assert.equal(JSON.stringify(schema),before);
});
test('plan/promise cannot terminate an execute or verification phase',()=>{
  for(const phase of ['discover','execute','verify'])assert.throws(()=>agent.check(value(state(phase)),history()),e=>e.decisionReason==='task_incomplete');
});
test('completion requires observed current-task evidence and no unfinished checklist',()=>{
  for(const s of [state('complete'),state('complete',{evidence_call_ids:['invented']}),state('complete',{evidence_call_ids:['one'],remaining:['run tests']}),state('complete',{evidence_call_ids:['one'],next_step:'verify later'})])assert.throws(()=>agent.check(value(s),history()),e=>e.decisionReason==='unverified_completion');
  assert.equal(agent.check(value(state('complete',{evidence_call_ids:['one']})),history()).phase,'complete');
});
test('plain answers and generated prose require no artificial execution evidence',()=>{
  assert.equal(agent.check(value(state('answer')),{messages:[{role:'user',content:'Explain DNS.'}]}).phase,'answer');
});
test('do not claim completion before selected tools have actually run',()=>{
  const c={name:'opaque',arguments:'{"path":"fixture.txt"}'};
  assert.throws(()=>agent.check(value(state('complete',{evidence_call_ids:['one']}),[c]),history()),e=>e.decisionReason==='unverified_completion');
  assert.equal(agent.check(value(state('execute',{remaining:['verify']}),[c]),history()).phase,'execute');
});
test('waiting and blockers must state an actual unfinished step, not an empty refusal',()=>{
  for(const phase of ['waiting','blocked'])assert.throws(()=>agent.check(value(state(phase)),history()),e=>e.decisionReason==='task_incomplete');
  assert.equal(agent.check(value(state('blocked',{remaining:['connect'],next_step:'Configure the declared private reference.'})),{messages:[]}).phase,'blocked');
  assert.equal(agent.check(value(state('waiting',{remaining:['job result'],next_step:'Read the existing job status.',evidence_call_ids:['one']})),history()).phase,'waiting');
});
test('same empty check is corrected before a third client execution',()=>{
  const data={messages:[{role:'user',content:'Read the existing job.'},call('one'),result('one',''),call('two'),result('two','')]};
  const same={name:'opaque',arguments:'{"path":"fixture.txt"}'};
  assert.throws(()=>agent.check(value(state('discover',{remaining:['diagnosis']}),[same]),data),e=>e.decisionReason==='stalled_decision');
  assert.equal(agent.check(value(state('discover'),[{name:'opaque',arguments:'{"path":"job-status.txt"}'}]),data).phase,'discover');
});
test('state changes and changed output allow a fresh verification',()=>{
  const data=history();data.messages.push(call('two'),result('two','new state'));
  assert.equal(agent.observe(data).repeats,1);
  assert.doesNotThrow(()=>agent.check(value(state('verify'),[{name:'opaque',arguments:'{"path":"fixture.txt"}'}]),data));
});
test('continued task retains evidence but new user task cannot borrow its receipts',()=>{
  const data=history();data.messages.push({role:'user',content:'<user_input mode="act">lanjut</user_input>'});
  assert.ok(agent.observe(data).ids.has('one'));
  data.messages.push({role:'user',content:'Build a different application.'});
  assert.ok(!agent.observe(data).ids.has('one'));
});
test('latest explicit failed check prevents a passing completion claim',()=>{
  const data=history();data.messages.push(call('tests'),result('tests','{"exit_code":1,"output":"synthetic failure"}'));
  assert.throws(()=>agent.check(value(state('complete',{evidence_call_ids:['tests']})),data),e=>e.decisionReason==='unverified_completion');
  data.messages.push(call('retry'),result('retry','{"exit_code":0,"output":"assertions passed"}'));
  assert.doesNotThrow(()=>agent.check(value(state('complete',{evidence_call_ids:['retry']})),data));
});
test('observations and phase metadata contain no tool text, arguments or private values',()=>{
  const data=history(),before=JSON.stringify(data);data.messages[2].content='synthetic-private-technical-output';
  const meta=agent.check(value(state('complete',{evidence_call_ids:['one']})),data);
  assert.ok(!JSON.stringify(meta).includes('synthetic-private-technical-output'));
  assert.ok(!agent.instructions(data).includes('synthetic-private-technical-output'));
  assert.equal(meta.phase_source,'model_declared');
  data.messages[2].content='{"ok":true}';assert.equal(JSON.stringify(data),before);
});
test('controller failures use one bounded unexecuted decision repair, retaining access checks',async()=>{
  let count=0;
  const out=await recovery.chooseDecision(async()=>{
    count++;const raw=JSON.stringify(value(state(count===1?'execute':'answer')));
    return recovery.parseResult(raw,v=>{agent.check(v,{messages:[]});return v;},{});
  },{});
  assert.equal(count,2);assert.equal(out.task_state.phase,'answer');
  assert.match(recovery.repairInstruction('stalled_decision'),/TASK DECISION CORRECTION/);
});
test('failed cited result cannot become passing evidence after an unrelated read',()=>{
  const data=history();data.messages.push(call('bad'),result('bad','{"exit_code":1}'),call('read'),result('read','source inspected'));
  assert.throws(()=>agent.check(value(state('complete',{evidence_call_ids:['bad']})),data),e=>e.decisionReason==='unverified_completion');
  assert.throws(()=>agent.check(value(state('complete',{evidence_call_ids:['bad','read']})),data),e=>e.decisionReason==='unverified_completion');
  data.messages.push(call('fixed'),result('fixed','{"exit_code":0}'));
  assert.doesNotThrow(()=>agent.check(value(state('complete',{evidence_call_ids:['fixed']})),data));
});
test('empty-only completion needs a corroborating nonempty receipt; empty diff remains valid with proof',()=>{
  const data={messages:[{role:'user',content:'Verify this project.'},call('empty'),result('empty','')]};
  assert.throws(()=>agent.check(value(state('complete',{evidence_call_ids:['empty']})),data),e=>e.decisionReason==='unverified_completion');
  data.messages.push(call('checked'),result('checked','{"exit_code":0,"passed":4}'));
  assert.doesNotThrow(()=>agent.check(value(state('complete',{evidence_call_ids:['empty','checked']})),data));
});
test('explicit native metadata, MCP errors, string exit and case-insensitive status are recognized',()=>{
  for(const body of [{exit_code:'1'},{status:'FAILED'},{isError:true},{content:[{type:'text',text:'fixture'}],metadata:{exit:1}},{output:'fixture',metadata:{metadata:{exitCode:2}}}]){
    const data=history();data.messages.push(call('bad'),result('bad',JSON.stringify(body)));
    assert.equal(agent.observe(data).last.failed,true);
    assert.throws(()=>agent.check(value(state('complete',{evidence_call_ids:['bad']})),data),e=>e.decisionReason==='unverified_completion');
  }
  for(const body of ['const example = {exit_code:1};','Sample log says error: expected fixture','{"content":[{"type":"text","text":"exit 1 is sample data"}],"metadata":{"exit":0}}']){
    const data=history();data.messages.push(call('read'),result('read',body));assert.equal(agent.observe(data).last.failed,false);
  }
});
test('polite continuations preserve receipts while new tasks remain isolated',()=>{
  for(const phrase of ['silakan lanjutkan','silahkan lanjutkan pekerjaannya','tolong teruskan','please continue','proceed','go on','keep going','lanjut terus','lanjutkan tugas yang belum selesai']){
    const data=history();data.messages.push({role:'user',content:phrase});assert.ok(agent.observe(data).ids.has('one'),phrase);
  }
  for(const phrase of ['continue with the next feature','lanjutkan membuat aplikasi berbeda','silakan audit server baru']){
    const data=history();data.messages.push({role:'user',content:phrase});assert.ok(!agent.observe(data).ids.has('one'),phrase);
  }
});
test('debugging guide covers contract-derived regressions and forbids claiming held-out tests',()=>{
  const guide=agent.instructions(history());
  assert.match(guide,/REPOSITORY DEBUGGING CONTRACT/);assert.match(guide,/concurrency and state transitions/);
  assert.match(guide,/Preserve original assertions/);assert.match(guide,/do not search outside scope for them or claim they passed/);
  assert.ok(!guide.includes('synthetic-private-technical-output'));
});
