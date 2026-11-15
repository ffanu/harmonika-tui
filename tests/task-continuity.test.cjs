'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const agent=require('../backend/planner/agent-controller.cjs');
const c=require('../backend/planner/task-continuity.cjs');
function history(entries,prompt='Perbaiki bug aplikasi dan jalankan tes.'){
 return {messages:[{role:'user',content:prompt},...entries.flatMap(([name,args,result],i)=>[
  {role:'assistant',tool_calls:[{id:'r'+i,function:{name,arguments:JSON.stringify(args)}}]},
  {role:'tool',tool_call_id:'r'+i,content:JSON.stringify(result)}])]};
}
const complete=(ids=[])=>({content:'Pemeriksaan selesai.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:ids,next_step:''}});
const next=(name,args)=>({content:'Rekonsiliasi hasil aktual.',tool_calls:[{name,arguments:JSON.stringify(args)}],task_state:{phase:'verify',remaining:['verify'],evidence_call_ids:[],next_step:''}});
const submit=['fixture.submit',{request_id:'opaque_request'},{status:'outcome_unknown',request_id:'opaque_request',job_id:'opaque_job'}];
test('a later unrelated read never resolves a lost acknowledgement or permits completion',()=>{
 const data=history([submit,['fixture.read',{path:'README.md'},{content:'read'}]]);
 assert.equal(c.summary(agent.observe(data)).unresolved_operations,1);
 assert.throws(()=>agent.check(complete(['r1']),data),e=>e.decisionReason==='unverified_completion');
});
test('uncertain mutation cannot be replayed with the same or a replacement request key',()=>{
 const data=history([submit,['fixture.read',{path:'README.md'},{content:'read'}]]);
 for(const request_id of ['opaque_request','new_key'])assert.throws(()=>agent.check(next('fixture.submit',{request_id}),data),e=>e.decisionReason==='stalled_decision');
 assert.doesNotThrow(()=>agent.check(next('fixture.lookup',{job_id:'opaque_job'}),data));
});
test('only matching operation reconciliation clears uncertainty; conflicting foreign IDs do not',()=>{
 for(const receipt of [{status:'completed',job_id:'other_job'},{status:'completed',job_id:'other_job',request_id:'opaque_request'}]){
  const data=history([submit,['fixture.lookup',{job_id:'other_job'},receipt]]);
  assert.throws(()=>agent.check(complete(['r1']),data),e=>e.decisionReason==='unverified_completion');
 }
 const data=history([submit,['fixture.lookup',{job_id:'opaque_job'},{status:'completed',job_id:'opaque_job',request_id:'opaque_request'}]],'Verifikasi job fixture.');
 assert.doesNotThrow(()=>agent.check(complete(['r1']),data));
});
test('three unchanged status reads remain bounded across unrelated nonempty reads',()=>{
 const entries=[];for(let i=0;i<3;i++)entries.push(['fixture.status',{job_id:'j'},{status:'running',job_id:'j'}],['fixture.read',{path:'note'+i},{content:'unchanged'}]);
 assert.throws(()=>agent.check(next('fixture.status',{job_id:'j'}),history(entries)),e=>e.decisionReason==='stalled_decision');
 entries.push(['fixture.status',{job_id:'j'},{status:'running',job_id:'j',progress:2}]);
 assert.doesNotThrow(()=>agent.check(next('fixture.status',{job_id:'j'}),history(entries)));
});
test('tests before the latest code edit are stale, even if an unrelated read succeeds',()=>{
 const entries=[['fixture.test',{}, {exit_code:0,collected:2,passed:2}],['fixture.edit',{path:'src.js'},{ok:true}],['fixture.read',{path:'README.md'},{content:'read'}]];
 assert.throws(()=>agent.check(complete(['r0','r2']),history(entries)),e=>e.decisionReason==='unverified_completion');
 entries.push(['fixture.test',{}, {exit_code:0,collected:2,passed:2}]);
 assert.doesNotThrow(()=>agent.check(complete(['r3']),history(entries)));
});
test('post-edit readback is valid for correction without an explicit test request, only for its changed file',()=>{
 const entries=[['fixture.edit',{path:'src.js'},{ok:true}],['fixture.read',{path:'README.md'},{content:'read'}]];
 assert.throws(()=>agent.check(complete(['r1']),history(entries,'Perbaiki file src.js.')),e=>e.decisionReason==='unverified_completion');
 entries.push(['fixture.read',{path:'src.js'},{content:'new code'}]);
 assert.doesNotThrow(()=>agent.check(complete(['r2']),history(entries,'Perbaiki file src.js.')));
});
test('zero collected tests cannot pass via exit0 or a subsequent README read',()=>{
 const entries=[['fixture.test',{}, {exit_code:0,collected:0,passed:0}],['fixture.read',{path:'README.md'},{content:'read'}]];
 assert.throws(()=>agent.check(complete(['r1']),history(entries)),e=>e.decisionReason==='unverified_completion');
 entries.push(['fixture.test',{}, {exit_code:0,collected:2,passed:2}]);
 assert.doesNotThrow(()=>agent.check(complete(['r2']),history(entries)));
});
test('task-local mechanics and intent survive compaction without client supplied checkpoint authority',()=>{
 const original=history([submit]);const compacted={messages:[{role:'user',content:'lanjut'}],checkpoint:{unresolved_operations:0,mode:'conversation'}};
 agent.bindPrepared(original,compacted);
 assert.equal(agent.requestMode(compacted),'tool_action');assert.equal(c.summary(agent.observe(compacted)).unresolved_operations,1);
 assert.throws(()=>agent.check(complete(['r0']),compacted),e=>e.decisionReason==='unverified_completion');
 const newTask={messages:[{role:'user',content:'Apa fungsi DNS?'}]};assert.equal(c.summary(agent.observe(newTask)).unresolved_operations,0);
});
test('source/log strings and document arrays never become operation identities or mechanical failures',()=>{
 const details=c.inspect('fixture.read',{path:'document.json'},'const example = {status:"outcome_unknown", job_id:"private_value"}');
 assert.equal(details.pending,false);assert.deepEqual(details.references,{job:[],operation:[],request:[],key:[]});
 const data=history([['fixture.read',{path:'private_path'},{content:'private_source'}]],'Jelaskan fungsi file.');
 const summary=JSON.stringify(c.summary(agent.observe(data)));assert.ok(!summary.includes('private_'));assert.ok(!agent.instructions(data).includes('private_source'));
});
test('a status response for a foreign job cannot borrow the requested job ID from arguments',()=>{
 const data=history([submit,['fixture.lookup',{job_id:'opaque_job'},{status:'completed',job_id:'foreign_job'}]]);
 assert.equal(agent.observe(data).last.failed,true);
 assert.throws(()=>agent.check(complete(['r1']),data),e=>e.decisionReason==='unverified_completion');
});
test('different server request IDs and idempotency keys have independent namespaces',()=>{
 const details=c.inspect('fixture.submit',{idempotency_key:'client-key'},{status:'queued',request_id:'server-request',job_id:'job'});
 assert.equal(details.correlation_conflict,false);
});
test('native Cline cd-prefix test commands verify the edit, while echoes/build-only do not claim tests',()=>{
 const query='cd /private/fixture && npm test';
 const entries=[['editor',{path:'src.js',new_text:'fixed'},{ok:true}],['run_commands',{commands:[query]},[{query,success:true,result:'# tests 2\n# pass 2\n# fail 0\n'}]]];
 assert.doesNotThrow(()=>agent.check(complete(['r1']),history(entries)));
 for(const command of ['echo npm test','cd /tmp && npm run build'])assert.equal(c.inspect('run_commands',{commands:[command]},null).test,false);
});
test('count-less test receipts cannot establish an explicit test requirement',()=>{
 const entries=[['fixture.edit',{path:'src.js'},{ok:true}],['fixture.test',{}, {exit_code:0}],['fixture.read',{path:'src.js'},{content:'new'}]];
 assert.throws(()=>agent.check(complete(['r1','r2']),history(entries)),e=>e.decisionReason==='unverified_completion');
});
test('terminal status without a result identity does not borrow identity from its request',()=>{
 const data=history([submit,['fixture.lookup',{job_id:'opaque_job'},{status:'completed'}]]);
 assert.throws(()=>agent.check(complete(['r1']),data),e=>e.decisionReason==='unverified_completion');
});
test('a failed latest test remains failed after an unrelated read under polite prefixes',()=>{
 const data=history([['fixture.test',{}, {exit_code:1,collected:2,passed:1}],['fixture.read',{path:'README.md'},{content:'read'}]],'Sekarang tolong perbaiki bug aplikasi dan jalankan tes.');
 assert.throws(()=>agent.check(complete(['r1']),data),e=>e.decisionReason==='unverified_completion');
});
test('uncertain shell mutation still permits a bounded read-only diagnostic, without granting execution permission',()=>{
 const data=history([['run_commands',{commands:['fixture-deploy']},{status:'outcome_unknown',request_id:'r'}]]);
 assert.doesNotThrow(()=>agent.check(next('run_commands',{commands:['ps -p 123']}),data));
 for(const command of ['fixture-deploy','ps -p 123; fixture-deploy','ls $(fixture-deploy)'])assert.throws(()=>agent.check(next('run_commands',{commands:[command]}),data),e=>e.decisionReason==='stalled_decision');
});
test('all modified files need current readback or a fresh test, not only the last file',()=>{
 const entries=[['fixture.edit',{path:'a.js'},{ok:true}],['fixture.edit',{path:'b.js'},{ok:true}],['fixture.read',{path:'b.js'},{content:'b'}]];
 assert.throws(()=>agent.check(complete(['r2']),history(entries,'Perbaiki file aplikasi.')),e=>e.decisionReason==='unverified_completion');
 entries.push(['fixture.read',{path:'a.js'},{content:'a'}]);assert.doesNotThrow(()=>agent.check(complete(['r2','r3']),history(entries,'Perbaiki file aplikasi.')));
});
test('Cline user_input wrapper retains the original action and required verification',()=>{
 const data=history([['fixture.edit',{path:'a.js'},{ok:true}]],'<user_input mode="act">Perbaiki bug aplikasi dan jalankan tes.</user_input>');
 assert.equal(agent.requestMode(data),'tool_action');
 assert.throws(()=>agent.check(complete(['r0']),data),e=>e.decisionReason==='unverified_completion');
});
test('cancellation acknowledgement remains unresolved until terminal reconciliation',()=>{
 const data=history([['fixture.cancel',{job_id:'j'},{status:'cancel_requested',job_id:'j'}],['fixture.read',{path:'note'},{ok:true}]],'Verifikasi pembatalan job.');
 assert.throws(()=>agent.check(complete(['r1']),data),e=>e.decisionReason==='unverified_completion');
 data.messages.push({role:'assistant',tool_calls:[{id:'end',function:{name:'fixture.cancel_status',arguments:'{"job_id":"j"}'}}]}, {role:'tool',tool_call_id:'end',content:JSON.stringify({status:'completed',job_id:'j',worker_stopped:true,worker_state:'canceled'})});
 assert.doesNotThrow(()=>agent.check(complete(['end']),data));
});
test('operator-signed allowed tools narrow task scope; client policy fields do not grant authority',()=>{
 const data=history([]),contract={allowed_tools:['fixture.lookup'],required:['job'],checks:[]};
 assert.throws(()=>agent.check(next('fixture.edit',{path:'src.js',old:'a',new:'b'}),data,()=>contract),/tool_scope_denied/);
 assert.doesNotThrow(()=>agent.check(next('fixture.lookup',{job_id:'j'}),data,()=>contract));
 data.allowed_tools=['fixture.lookup'];
 assert.doesNotThrow(()=>agent.check(next('fixture.edit',{path:'src.js',old:'a',new:'b'}),data,()=>null));
 assert.throws(()=>agent.check(next('fixture.lookup',{job_id:'j'}),data,()=>({...contract,allowed_tools:['fixture.lookup','fixture.lookup']})),/tool_proof_contract_invalid/);
});
test('native Cline truncated display query correlates only to its unique exact original command',()=>{
 const command='cd /private/fixture && node --test && node -e '+JSON.stringify('synthetic boundary assertion '.repeat(18));
 const query=command.slice(0,200)+' ... [command truncated: '+(command.length-200)+' more chars; full command is in the tool call input]';
 const result=[{query,success:true,result:'TAP version 13\n# tests 2\n# pass 2\n# fail 0\n'}];
 assert.equal(c.inspect('run_commands',{commands:[command]},result).test_passed,true);
 const data=history([['editor',{path:'src.js',new_text:'corrected'},{ok:true}],['run_commands',{commands:[command]},result]]);
 assert.doesNotThrow(()=>agent.check(complete(['r1']),data));
 const bad=[{...result[0],query:query.replace('more chars','different chars')}];
 assert.equal(c.inspect('run_commands',{commands:[command]},bad).test_passed,false);
 assert.equal(c.inspect('run_commands',{commands:[command+'x']},result).test_passed,false);
 const other=command.slice(0,-1)+(command.endsWith('x')?'y':'x');
 assert.equal(c.inspect('run_commands',{commands:[command,other]},result).test_passed,false);
 assert.equal(c.inspect('fixture.read',{path:'source.js'},result).test_passed,false);
});
