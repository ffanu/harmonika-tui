'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const agent=require('../backend/planner/agent-controller.cjs'),continuity=require('../backend/planner/task-continuity.cjs'),scope=require('../backend/planner/verification-scope.cjs');
const history=(entries,prompt='Perbaiki bug aplikasi dan jalankan tes.')=>({messages:[{role:'user',content:prompt},...entries.flatMap(([name,args,result],i)=>[{role:'assistant',tool_calls:[{id:'r'+i,function:{name,arguments:JSON.stringify(args)}}]},{role:'tool',tool_call_id:'r'+i,content:JSON.stringify(result)}])]});
const complete=ids=>({content:'Pemeriksaan selesai.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:ids,next_step:''}});
const write=(file,metadata={},args={})=>['editor',{path:file,new_text:'fixed',...args},{ok:true,metadata}];
const check=metadata=>['fixture.test',{}, {exit_code:0,collected:2,passed:2,metadata}];
const tap='# tests 2\n# pass 2\n# fail 0\n';
const shell=(commands,args={})=>['run_commands',{commands,...args},commands.map(query=>({query,success:true,result:tap}))];
const denied=(entries,prompt)=>assert.throws(()=>agent.check(complete(['r'+(entries.length-1)]),history(entries,prompt)),e=>e.decisionReason==='unverified_completion');
const allowed=(entries,prompt)=>assert.doesNotThrow(()=>agent.check(complete(['r'+(entries.length-1)]),history(entries,prompt)));
for(const field of ['owner_id','project_id','workspace_id','target','attempt','revision'])test('explicit '+field+' contradiction cannot verify an edited file',()=>{
 denied([write('src/main.cjs',{[field]:'active'}),check({[field]:'archive'})]);
 allowed([write('src/main.cjs',{[field]:'active'}),check({[field]:'active'})]);
});
test('a write consumes the old revision and returns a new revision without a false conflict',()=>{
 allowed([write('src/main.cjs',{revision:'r2'},{revision:'r1'}),check({revision:'r2'})]);
 denied([write('src/main.cjs',{revision:'r2'},{revision:'r1'}),check({revision:'r1'})]);
});
test('test entry-point target arguments do not conflict with executor target identities',()=>{
 allowed([write('src/main.cjs',{target:'workspace-target'}),['fixture.test',{target:'test/contract.test.cjs'}, {exit_code:0,collected:2,passed:2,metadata:{target:'workspace-target'}}]]);
});
test('explicit workspace revisions advance all edited files in the same workspace',()=>{
 const meta=revision=>({project_id:'active',workspace_id:'workspace',revision,revision_scope:'workspace',cwd:'/tmp/app'});
 allowed([write('src/a.cjs',meta('r1')),write('src/b.cjs',meta('r2')),check(meta('r2'))]);
 denied([write('src/a.cjs',meta('r1')),write('src/b.cjs',meta('r2')),check(meta('r1'))]);
});
test('file revisions never inherit a different file revision solely because project IDs match',()=>{
 const meta=revision=>({project_id:'active',revision,revision_scope:'file'});
 denied([write('a.cjs',meta('r1')),write('b.cjs',meta('r2')),check(meta('r2'))]);
});
test('source_revision aliases the explicit verification revision without discarding contradictions',()=>{
 denied([write('main.cjs',{revision:'new'}),check({source_revision:'old'})]);
 allowed([write('main.cjs',{revision:'new'}),check({source_revision:'new'})]);
 denied([write('main.cjs',{revision:'new'}),check({revision:'new',source_revision:'old'})]);
});
for(const cwd of ['/tmp/archive','/tmp/application','/tmp/app/../archive'])test('foreign lexical test cwd '+cwd+' does not cover edited target',()=>{
 denied([write('/tmp/app/src/main.cjs'),shell(['cd '+cwd+' && node --test'])]);
});
test('same explicit POSIX project including spaces and dot segments remains valid',()=>{
 allowed([write('/tmp/my app/src/main.cjs'),shell(['cd "/tmp/my app/./" && node --test'])]);
});
test('relative edited paths resolve only against the explicit source cwd',()=>{
 allowed([write('src/main.cjs',{}, {cwd:'/tmp/app'}),shell(['cd /tmp/app && node --test'])]);
 denied([write('src/main.cjs',{}, {cwd:'/tmp/app'}),shell(['cd /tmp/archive && node --test'])]);
});
test('one passing project test cannot validate two project writes',()=>{
 denied([write('/tmp/a/src/a.cjs'),write('/tmp/b/src/b.cjs'),shell(['cd /tmp/b && node --test'])]);
 allowed([write('/tmp/a/src/a.cjs'),write('/tmp/b/src/b.cjs'),shell(['cd /tmp/a && node --test','cd /tmp/b && node --test'])]);
});
test('identical relative paths in separate projects never collapse into one verification obligation',()=>{
 denied([write('src/main.cjs',{}, {cwd:'/tmp/a'}),write('src/main.cjs',{}, {cwd:'/tmp/b'}),shell(['cd /tmp/b && node --test'])]);
 allowed([write('src/main.cjs',{}, {cwd:'/tmp/a'}),write('src/main.cjs',{}, {cwd:'/tmp/b'}),shell(['cd /tmp/a && node --test','cd /tmp/b && node --test'])]);
});
test('new revisions of the same normalized target supersede old writes without keeping stale obligations',()=>{
 allowed([write('/tmp/app/src/./main.cjs',{revision:'r1'}),write('/tmp/app/src/main.cjs',{revision:'r2'}),shell(['cd /tmp/app && node --test'])]);
});
test('native cwd argument is preserved in each batch child',()=>{
 denied([write('/tmp/a/src/a.cjs'),shell(['node --test'],{cwd:'/tmp/b'})]);
 allowed([write('/tmp/a/src/a.cjs'),shell(['node --test'],{cwd:'/tmp/a'})]);
});
test('Windows drive and UNC normalization stay within their own lexical project',()=>{
 allowed([write('C:\\app\\src\\main.cjs'),shell(['cd /d "c:\\app" && node --test'])]);
 denied([write('C:\\app\\src\\main.cjs'),shell(['cd /d "C:\\archive" && node --test'])]);
 allowed([write('\\\\host\\share\\app\\src\\main.cjs'),shell(['cd "\\\\host\\share\\app" && node --test'])]);
});
for(const command of ['cd "$PROJECT" && node --test','cd /tmp/app && node --test && cd /tmp/archive && node --test','cd / && node --test','cd /tmp/app && npm test --workspace archive'])test('ambiguous root selection is not broad target verification: '+command,()=>{
 denied([write('/tmp/app/src/main.cjs'),shell([command])]);
});
test('old readback revision cannot verify the new write',()=>{
 const entries=[write('main.cjs',{revision:'new'}),['fixture.read',{path:'main.cjs'},{path:'main.cjs',content:'fixed',metadata:{revision:'old'}}]];
 denied(entries,'Perbaiki file main.cjs.');entries[1][2].metadata.revision='new';allowed(entries,'Perbaiki file main.cjs.');
});
test('a pinned historical read cannot verify an unpinned new write',()=>{
 denied([write('main.cjs'),['fixture.read',{path:'main.cjs',ref:'HEAD~2'},{content:'old'}]],'Perbaiki file main.cjs.');
});
test('full-file writes require matching content when a full readback is supplied',()=>{
 denied([['fs.write',{path:'/tmp/app/main.cjs',content:'fixed'},{ok:true}],['fs.read',{path:'/tmp/app/main.cjs'},{content:'old'}]],'Perbaiki file main.cjs.');
 allowed([['fs.write',{path:'/tmp/app/main.cjs',content:'fixed'},{ok:true}],['fs.read',{path:'/tmp/app/main.cjs'},{content:'fixed'}]],'Perbaiki file main.cjs.');
});
for(const field of ['owner_id','project_id','workspace_id'])test('same job ID under another '+field+' does not settle the original operation',()=>{
 const entries=[['fixture.submit',{}, {status:'outcome_unknown',job_id:'job',[field]:'active'}],['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',[field]:'archive'}]];
 denied(entries,'Verifikasi job fixture.');entries.push(['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',[field]:'active'}]);allowed(entries,'Verifikasi job fixture.');
});
test('caller-requested scope conflicting with returned metadata is marked failed',()=>{
 const d=history([['fixture.test',{project_id:'active'}, {exit_code:0,collected:2,passed:2,metadata:{project_id:'archive'}}]]);
 assert.equal(agent.observe(d).last.failed,true);denied([['fixture.test',{project_id:'active'}, {exit_code:0,collected:2,passed:2,metadata:{project_id:'archive'}}]]);
});
test('later pending scope enriches the original operation and cannot be erased by a foreign terminal lookup',()=>{
 const entries=[['fixture.submit',{}, {status:'outcome_unknown',job_id:'job'}],['fixture.status',{job_id:'job'},{status:'running',job_id:'job',project_id:'active'}],['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',project_id:'foreign'}]];
 denied(entries,'Verifikasi job fixture.');
 entries.push(['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',project_id:'active'}]);allowed(entries,'Verifikasi job fixture.');
});
test('scope mechanics never interpret JSON/source content as identities or export raw paths/IDs',()=>{
 const raw={project_id:'private_document_project',revision:'private_document_revision'};
 assert.deepEqual(scope.inspect('fixture.read',{},raw,{read:true}).ids,{});
 const d=history([write('/tmp/private_project/src.js',{project_id:'private_project',revision:'private_revision'})]);
 const encoded=JSON.stringify(agent.observe(d));assert.ok(!encoded.includes('private_'));
});
test('unknown legacy scope is preserved as client_reported compatibility, not upgraded to attestation',()=>{
 allowed([write('src/main.cjs'),check({})]);
 const result=agent.check(complete(['r1']),history([write('src/main.cjs'),check({})]));assert.equal(result.receipt_source,'client_reported');assert.equal(result.verified_check_count,0);
});
test('contradictory scope survives internal compaction',()=>{
 const d=history([write('src/main.cjs',{project_id:'active'}),check({project_id:'archive'})]),prepared={messages:[{role:'user',content:'lanjut'}]};
 agent.bindPrepared(d,prepared);assert.throws(()=>agent.check(complete(['r1']),prepared),e=>e.decisionReason==='unverified_completion');
});
