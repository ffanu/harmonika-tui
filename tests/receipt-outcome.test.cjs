'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const a=require('../backend/planner/agent-controller.cjs'),c=require('../backend/planner/task-continuity.cjs');
const history=entries=>({messages:[{role:'user',content:'Verifikasi fixture.'},...entries.flatMap(([name,args,result],i)=>[{role:'assistant',tool_calls:[{id:'r'+i,function:{name,arguments:JSON.stringify(args)}}]},{role:'tool',tool_call_id:'r'+i,content:result===null?null:JSON.stringify(result)}])]});
const complete=ids=>({content:'Pemeriksaan selesai.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:ids,next_step:''}});
const next=(name,args)=>({content:'Pemeriksaan aktual.',tool_calls:[{name,arguments:JSON.stringify(args)}],task_state:{phase:'verify',remaining:['verify'],evidence_call_ids:[],next_step:''}});
const read=['fixture.read',{path:'README.md'},{content:'fixture'}];
for(const result of [{status:'in_queue',job_id:'job'},{status:'retrying',job_id:'job'},{success:true,job_id:'job'},null,'accepted'])test('unknown/missing operation outcome survives an unrelated successful read: '+JSON.stringify(result),()=>{
 const d=history([['fixture.submit',{idempotency_key:'key'},result],read]);
 assert.throws(()=>a.check(complete(['r1']),d),e=>e.decisionReason==='unverified_completion');
 assert.throws(()=>a.check(next('fixture.submit',{idempotency_key:'replacement'}),d),e=>e.decisionReason==='stalled_decision');
 d.messages.push({role:'assistant',tool_calls:[{id:'terminal',function:{name:'fixture.lookup',arguments:'{"idempotency_key":"key"}'}}]},{role:'tool',tool_call_id:'terminal',content:'{"status":"completed","job_id":"job","idempotency_key":"key"}'});
 assert.doesNotThrow(()=>a.check(complete(['terminal']),d));
});
test('unknown array child keeps its original identity and scope for reconciliation',()=>{
 const entries=[['run_commands',{commands:['fixture-submit','ls']},[{query:'fixture-submit',success:true,result:'ack',metadata:{status:'in_queue',job_id:'job',project_id:'active'}},{query:'ls',success:true,result:'fixture'}]],read];
 assert.throws(()=>a.check(complete(['r1']),history(entries)),e=>e.decisionReason==='unverified_completion');
 entries.push(['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',project_id:'foreign'}]);
 assert.throws(()=>a.check(complete(['r2']),history(entries)),e=>e.decisionReason==='unverified_completion');
 entries.push(['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',project_id:'active'}]);assert.doesNotThrow(()=>a.check(complete(['r3']),history(entries)));
});
for(const marker of [{partial:true},{truncated:true},{is_truncated:true},{output_limited:true},{complete:false}])test('partial positive test counts never prove completion: '+JSON.stringify(marker),()=>{
 const entries=[['fixture.test',{}, {exit_code:0,collected:2,passed:2,metadata:marker}]];
 assert.throws(()=>a.check(complete(['r0']),history(entries)),e=>e.decisionReason==='unverified_completion');
 assert.doesNotThrow(()=>a.check(next('fixture.test',{}),history(entries)));
 entries.push(['fixture.test',{}, {exit_code:0,collected:2,passed:2}]);assert.doesNotThrow(()=>a.check(complete(['r1']),history(entries)));
});
test('partial process output without a known exit and with an operation ID requires lookup, not another test worker',()=>{
 const d=history([['run_commands',{commands:['node --test']},[{query:'node --test',success:true,result:'partial TAP',metadata:{truncated:true,job_id:'job'}}]]]);
 assert.throws(()=>a.check(next('run_commands',{commands:['node --test']}),d),e=>e.decisionReason==='stalled_decision');
 assert.doesNotThrow(()=>a.check(next('fixture.lookup',{job_id:'job'}),d));
});
test('known exited partial native test can obtain another full receipt',()=>{
 const d=history([['run_commands',{commands:['node --test']},[{query:'node --test',success:true,result:'# tests 2\n# pass 2\n# fail 0\n',exit_code:0,truncated:true}]]]);
 assert.equal(a.observe(d).last.continuity.test_passed,false);
 assert.doesNotThrow(()=>a.check(next('run_commands',{commands:['node --test']}),d));
});
test('partial reads are retryable but cannot be cited as verification',()=>{
 const d=history([['fixture.read',{path:'source.cjs'},{content:'partial',metadata:{truncated:true}}]]);
 assert.throws(()=>a.check(complete(['r0']),d),e=>e.decisionReason==='unverified_completion');
 assert.doesNotThrow(()=>a.check(next('fixture.read',{path:'source.cjs'}),d));
});
test('partial terminal job status does not clear an unknown operation',()=>{
 const d=history([['fixture.submit',{}, {status:'outcome_unknown',job_id:'job'}],['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',complete:false}],read]);
 assert.throws(()=>a.check(complete(['r2']),d),e=>e.decisionReason==='unverified_completion');
});
test('a full-file write acknowledgement is reconciled only by exact complete same-file readback',()=>{
 const write=['fs.write',{path:'/tmp/app/main.cjs',content:'fixed'},{ok:true,metadata:{partial:true}}];
 const good=['fs.read',{path:'/tmp/app/main.cjs'},{content:'fixed'}];
 for(const bad of [['fs.read',{path:'/tmp/foreign/main.cjs'},{content:'fixed'}],['fs.read',{path:'/tmp/app/main.cjs'},{content:'different'}],['fs.read',{path:'/tmp/app/main.cjs'},{content:'fixed',metadata:{partial:true}}]])assert.throws(()=>a.check(complete(['r1']),history([write,bad])),e=>e.decisionReason==='unverified_completion');
 assert.doesNotThrow(()=>a.check(complete(['r1']),history([write,good])));
 assert.throws(()=>a.check(next('editor',{path:'/tmp/app/main.cjs',new_text:'fixed'}),history([write])),e=>e.decisionReason==='stalled_decision');
});
test('file readback never settles an asynchronous operation reference even with matching bytes',()=>{
 const d=history([['fs.write',{path:'/tmp/app/main.cjs',content:'fixed'},{ok:true,job_id:'job',metadata:{partial:true}}],['fs.read',{path:'/tmp/app/main.cjs'},{content:'fixed'}]]);
 assert.throws(()=>a.check(complete(['r1']),d),e=>e.decisionReason==='unverified_completion');
});
test('create/save/write actions require verification even when no fix/build verb was used',()=>{
 for(const prompt of ['Simpan file main.cjs.','Tulis file main.cjs.','Buat file main.cjs.']){
  const d=history([['fs.write',{path:'/tmp/app/main.cjs',content:'fixed'},{ok:true}]]);d.messages[0].content=prompt;
  assert.throws(()=>a.check(complete(['r0']),d),e=>e.decisionReason==='unverified_completion');
  d.messages.push({role:'assistant',tool_calls:[{id:'readback',function:{name:'fs.read',arguments:'{"path":"/tmp/app/main.cjs"}'}}]},{role:'tool',tool_call_id:'readback',content:'{"content":"fixed"}'});
  assert.doesNotThrow(()=>a.check(complete(['readback']),d));
 }
});
test('full-file verification does not accept an absent content hash as an exact match',()=>{
 const d=history([['fs.write',{path:'/tmp/app/main.cjs',content:'fixed'},{ok:true}],['fs.read',{path:'/tmp/app/main.cjs'},'stale plain output']]);
 assert.throws(()=>a.check(complete(['r1']),d),e=>e.decisionReason==='unverified_completion');
});
test('tests before an uncertain write remain stale after its content has been reconciled',()=>{
 const d=history([['fixture.test',{}, {exit_code:0,collected:2,passed:2}],['fs.write',{path:'/tmp/app/main.cjs',content:'fixed'},{ok:true,partial:true}],['fs.read',{path:'/tmp/app/main.cjs'},{content:'fixed'}]]);d.messages[0].content='Perbaiki file dan jalankan tes.';
 assert.throws(()=>a.check(complete(['r0','r2']),d),e=>e.decisionReason==='unverified_completion');
});
test('ordinary synchronous Cline receipts and request trace IDs retain compatibility',()=>{
 const m=c.inspect('run_commands',{commands:['pwd']},[{query:'pwd',success:true,result:'/tmp/app',request_id:'trace'}]);assert.equal(m.pending,false);
 const proposed=c.inspect('fixture.submit',{idempotency_key:'key'},null);assert.equal(proposed.pending,false);
});
test('source/status-like data is not an executor outcome, and mechanics never expose raw values',()=>{
 assert.equal(c.inspect('fixture.read',{path:'data.json'},{status:'in_queue',job_id:'private_job',partial:true}).pending,false);
 assert.equal(c.inspect('fixture.read',{path:'data.json'},'const data={partial:true,status:"in_queue"}').receipt_incomplete,false);
 const d=history([['fixture.submit',{}, {status:'private_unknown_state',job_id:'private_job'}]]);
 assert.ok(!JSON.stringify(a.observe(d)).includes('private_'));
});
test('uncertain state survives internal compaction',()=>{
 const d=history([['fixture.submit',{}, {success:true,job_id:'job'}]]),prepared={messages:[{role:'user',content:'lanjut'}]};a.bindPrepared(d,prepared);
 assert.equal(c.summary(a.observe(prepared)).unresolved_operations,1);
 assert.throws(()=>a.check(complete(['r0']),prepared),e=>e.decisionReason==='unverified_completion');
});
