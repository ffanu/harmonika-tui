'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const agent=require('../backend/planner/agent-controller.cjs'),c=require('../backend/planner/task-continuity.cjs');
const history=(commands,result)=>({messages:[{role:'user',content:'Verifikasi job fixture.'},
 {role:'assistant',tool_calls:[{id:'batch',function:{name:'run_commands',arguments:JSON.stringify({commands})}}]},
 {role:'tool',tool_call_id:'batch',content:JSON.stringify(result)}]});
const append=(d,name,args,result,id='after')=>d.messages.push({role:'assistant',tool_calls:[{id,function:{name,arguments:JSON.stringify(args)}}]},{role:'tool',tool_call_id:id,content:JSON.stringify(result)});
const complete=ids=>({content:'Pemeriksaan selesai.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:ids,next_step:''}});
const next=commands=>({content:'Periksa keadaan aktual.',tool_calls:[{name:'run_commands',arguments:JSON.stringify({commands})}],task_state:{phase:'verify',remaining:['verify'],evidence_call_ids:[],next_step:''}});
const receipt=(query,extra={})=>({query,success:true,result:'fixture',...extra});
const tap='# tests 2\n# pass 2\n# fail 0\n';
test('a successful non-test mutation is not replayed because its sibling check failed',()=>{
 const d=history(['./fixture-deploy','node --test'],[receipt('./fixture-deploy'),receipt('node --test',{result:'# tests 2\n# pass 1\n# fail 1\n'})]);
 for(const commands of [['./fixture-deploy'],['node --test','./fixture-deploy']])assert.throws(()=>agent.check(next(commands),d),e=>e.decisionReason==='stalled_decision');
 assert.doesNotThrow(()=>agent.check(next(['node --test']),d));
 assert.doesNotThrow(()=>agent.check(next(['ps -p 123']),d));
 const newTask={messages:[{role:'user',content:'Jalankan deployment fixture untuk revisi baru.'}]};assert.doesNotThrow(()=>agent.check(next(['./fixture-deploy']),newTask));
});
for(const state of ['queued','running','outcome_unknown','cancel_requested'])test('child '+state+' survives a successful sibling and unrelated read',()=>{
 const d=history(['fixture-submit','ls'],[receipt('fixture-submit',{status:state,job_id:'job'}),receipt('ls')]);
 append(d,'fixture.read',{path:'README.md'},{content:'fixture'});
 assert.equal(c.summary(agent.observe(d)).unresolved_operations,1);
 assert.throws(()=>agent.check(complete(['after']),d),e=>e.decisionReason==='unverified_completion');
 append(d,'fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job'},'terminal');
 assert.doesNotThrow(()=>agent.check(complete(['terminal']),d));
});
test('per-command metadata and contradictory completed/running fields stay pending',()=>{
 const d=history(['fixture-submit','ls'],[receipt('fixture-submit',{status:'completed',metadata:{state:'running',job_id:'job'}}),receipt('ls')]);
 assert.equal(c.summary(agent.observe(d)).unresolved_operations,1);
});
test('repackaging an unknown child into another batch does not allow replay; safe diagnostic remains available',()=>{
 const d=history(['fixture-submit','ls'],[receipt('fixture-submit',{status:'outcome_unknown',job_id:'job'}),receipt('ls')]);
 for(const commands of [['fixture-submit'],['ls','fixture-submit'],['fixture-submit-new-key']])assert.throws(()=>agent.check(next(commands),d),e=>e.decisionReason==='stalled_decision');
 assert.doesNotThrow(()=>agent.check(next(['ps -p 123']),d));
});
for(const [label,items]of [
 ['missing',[receipt('ls')]],['foreign',[receipt('ls'),receipt('foreign')]],
 ['duplicate',[receipt('fixture-submit'),receipt('fixture-submit'),receipt('ls')]],
 ['malformed',[{query:'fixture-submit',result:'fixture'},receipt('ls')]],
 ['empty',[]],
])test(label+' batch envelope cannot establish completion or be replayed',()=>{
 const d=history(['fixture-submit','ls'],items);append(d,'fixture.read',{path:'README.md'},{content:'fixture'});
 assert.throws(()=>agent.check(complete(['after']),d),e=>e.decisionReason==='unverified_completion');
 assert.throws(()=>agent.check(next(['fixture-submit']),d),e=>e.decisionReason==='stalled_decision');
});
test('a successful test sibling cannot mask failed process metadata or transport',()=>{
 for(const extra of [{success:false},{exit_code:1},{metadata:{exit_code:1}},{metadata:{exit_code:'1'}},{metadata:{status:'failed'}}]){
  const m=c.inspect('run_commands',{commands:['node --test']},[receipt('node --test',{result:tap,...extra})]);
  assert.equal(m.execution_failed,true);assert.equal(m.test_passed,false);
 }
});
test('structured child test counts retain their own explicit exit and pass contract',()=>{
 const args={commands:['node --test']};
 assert.equal(c.inspect('run_commands',args,[receipt('node --test',{metadata:{collected:2,passed:2,exit_code:0}})]).test_passed,true);
 assert.equal(c.inspect('run_commands',args,[receipt('node --test',{metadata:{collected:2,passed:1,exit_code:1}})]).test_passed,false);
});
test('all distinct requested test envelopes must be present once, including truncated native query matching',()=>{
 const commands=['node --test test/a.cjs','node --test test/b.cjs'];
 const good=commands.map(query=>receipt(query,{result:tap}));
 assert.equal(c.inspect('run_commands',{commands},good).test_count,4);
 assert.equal(c.inspect('run_commands',{commands},good).test_passed,true);
 for(const items of [[good[0]],[good[0],good[0]]])assert.equal(c.inspect('run_commands',{commands},items).test_passed,false);
});
test('same read child cannot evade polling bound by changing sibling commands',()=>{
 const d=history(['ls','pwd'],[receipt('ls'),receipt('pwd')]);
 for(let i=0;i<2;i++)append(d,'run_commands',{commands:['ls','stat file'+i]},[receipt('ls'),receipt('stat file'+i)],'read'+i);
 assert.throws(()=>agent.check(next(['ls','ps -p 123']),d),e=>e.decisionReason==='stalled_decision');
});
test('ordinary source arrays and status-like output strings never become child operations',()=>{
 const source=[receipt('fixture-submit',{status:'outcome_unknown',job_id:'private_job'})];
 assert.equal(c.inspect('fixture.read',{path:'file.json'},source).children,undefined);
 const m=c.inspect('run_commands',{commands:['ls']},[receipt('ls',{result:'{"status":"outcome_unknown","job_id":"private_job"}'})]);
 assert.equal(m.pending,false);assert.ok(!JSON.stringify(c.summary(agent.observe(history(['ls'],[receipt('ls',{result:'private_source'})])))).includes('private_'));
});
test('unresolved children survive internal compaction without storing raw commands',()=>{
 const d=history(['fixture-submit','ls'],[receipt('fixture-submit',{status:'outcome_unknown',job_id:'private_job'}),receipt('ls')]);
 const compact={messages:[{role:'user',content:'lanjut'}]};agent.bindPrepared(d,compact);
 assert.equal(c.summary(agent.observe(compact)).unresolved_operations,1);
 assert.ok(!JSON.stringify(agent.observe(compact)).includes('fixture-submit'));
 assert.throws(()=>agent.check(complete(['batch']),compact),e=>e.decisionReason==='unverified_completion');
});
