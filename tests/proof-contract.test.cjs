'use strict';
const {test,after}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),cp=require('node:child_process');
const proof=require('../backend/planner/proof-contract.cjs'),agent=require('../backend/planner/agent-controller.cjs');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-proof-test-'));fs.chmodSync(root,0o700);after(()=>fs.rmSync(root,{recursive:true}));
const sha=x=>crypto.createHash('sha256').update(x).digest('hex'),key=crypto.randomBytes(32),keyPath=path.join(root,'key');fs.writeFileSync(keyPath,key.toString('hex'),{mode:0o600});
const now=Date.now(),id='a'.repeat(32),revision=sha('real fixture revision');
function fixture(kind='tests',facts={exit_code:0,collected:4,passed:4,failed:0,awaited:true,source_sha256:revision}){
 const content=JSON.stringify({ok:true,exit_code:0,mechanics:'synthetic'}),data={messages:[{role:'user',content:'Verify fixture [HarmonikaProof:'+id+']'},{role:'assistant',tool_calls:[{id:'check1',function:{name:'read_file',arguments:'{}'}}]},{role:'tool',tool_call_id:'check1',content}]};
 const v={version:1,id,task_hash:proof.taskHash(data.messages[0].content),issued_at:now-1000,expires_at:now+60000,owner:'b'.repeat(32),project:'c'.repeat(32),target:'d'.repeat(32),attempt:'e'.repeat(32),revision,required:[kind],checks:[]};
 const c={...Object.fromEntries(['owner','project','target','attempt','revision'].map(k=>[k,v[k]])),kind,sequence:1,at:now-100,provenance:'independently_verified',outcome:'passed',call_id:'check1',result_sha256:sha(JSON.stringify(content)),facts};v.checks=[c];return {data,v,c};
}
function signed(v){const payload=JSON.stringify(v);fs.writeFileSync(path.join(root,id+'.json'),JSON.stringify({payload,mac:crypto.createHmac('sha256',key).update(payload).digest('hex')}),{mode:0o600});}
const reader=proof.createReader({directory:root,keyPath,clock:()=>now});
function pass(f){signed(f.v);return proof.assess(reader(f.data),agent.observe(f.data),now).complete;}
function decide(f,phase='complete',content='EVERYTHING PASSED including unregistered features'){
 signed(f.v);const v={content,tool_calls:[],task_state:{phase,remaining:phase==='blocked'?['verify']:[],next_step:phase==='blocked'?'Read actual state.':'',evidence_call_ids:['check1']}};agent.check(v,f.data,reader);return v;
}
const cases=[
 ['E01',()=>{const file=path.join(root,'source');fs.writeFileSync(file,'fixture');const b=fs.readFileSync(file);assert.equal(pass(fixture('read',{bytes:b.length,sha256:sha(b),complete:true})),true)}],
 ['E02',()=>{assert.throws(()=>fs.readFileSync(path.join(root,'missing')));const f=fixture('read',{bytes:0,complete:false});assert.equal(pass(f),false)}],
 ['E03',()=>{const out=cp.spawnSync('/this-exam-command-does-not-exist',[]);assert.equal(out.error.code,'ENOENT');assert.equal(pass(fixture('tests',{})),false)}],
 ['E04',()=>{const out=cp.spawnSync(process.execPath,['-e','console.log("PASS");process.exit(4)']);assert.equal(out.status,4);const f=fixture();f.c.facts.exit_code=out.status;assert.equal(pass(f),false)}],
 ['E05',()=>{const f=fixture();Object.assign(f.c.facts,{collected:0,passed:0});assert.equal(pass(f),false)}],
 ['E06',()=>assert.throws(()=>decide(fixture(),'answer'),e=>e.decisionReason==='unverified_completion')],
 ['E07',()=>assert.equal(pass(fixture('vision',{image_bytes:0,model_completed:false})),false)],
 ['E08',()=>assert.equal(pass(fixture('tests',{metadata:{exit_code:0,collected:4,passed:4}})),false)],
 ['E09',()=>assert.equal(agent.check({content:'DNS maps names to records.',tool_calls:[],task_state:{phase:'answer',remaining:[],evidence_call_ids:[],next_step:''}},{messages:[{role:'user',content:'Explain DNS.'}]}).phase,'answer')],
 ['E10',()=>assert.equal(agent.check({content:'Rencana: baca source, jalankan tes, lalu periksa hasil.',tool_calls:[],task_state:{phase:'answer',remaining:[],evidence_call_ids:[],next_step:''}},{messages:[{role:'user',content:'Hanya buat rencana.'}]}).phase,'answer')],
 ['E11',()=>{const t=path.join(root,'real.test.cjs');fs.writeFileSync(t,"require('node:test').test('actual assertion',()=>require('node:assert/strict').equal(2+2,4))");const env={...process.env};delete env.NODE_TEST_CONTEXT;const out=cp.spawnSync(process.execPath,['--test','--test-reporter=tap',t],{encoding:'utf8',env});assert.equal(out.status,0);assert.match(out.stdout,/# pass 1/);const f=fixture();Object.assign(f.c.facts,{collected:1,passed:1});assert.equal(pass(f),true)}],
 ['E12',()=>assert.equal(pass(fixture('deploy',{http_status:200})),false)],
 ['M01',()=>{const f=fixture();f.v.revision=sha('new revision');assert.equal(pass(f),false)}],
 ['M02',()=>{const f=fixture();f.c.target='f'.repeat(32);assert.equal(pass(f),false)}],
 ['M03',()=>{const f=fixture();f.c.owner='f'.repeat(32);assert.equal(pass(f),false)}],
 ['M04',()=>assert.equal(pass(fixture('job',{status:'running'})),false)],
 ['M05',()=>assert.equal(pass(fixture('tests',{content:JSON.stringify({passed:true})})),false)],
 ['M06',()=>assert.equal(pass(fixture('deploy',{screenshot:true,http_status:200})),false)],
 ['M07',()=>assert.equal(pass(fixture('document',{pages:2,pages_read:2,missing_fields:0,contradictions:1})),false)],
 ['M08',()=>assert.equal(pass(fixture('deploy',{source_sha256:revision,pid:123,listener:true,tls_verified:true,functional:false})),false)],
 ['M09',()=>assert.equal(pass(fixture('document',{pages:2,pages_read:1,missing_fields:1,contradictions:0})),false)],
 ['M10',()=>{const f=fixture('liveness',{http_status:200,reachable:true});f.c.at=now-400000;assert.equal(pass(f),false)}],
 ['M11',()=>{const f=fixture();f.data.messages[0].content='Changed requirement [HarmonikaProof:'+id+']';signed(f.v);assert.throws(()=>reader(f.data))}],
 ['M12',()=>{const f=fixture();f.v.required.push('artifact');assert.equal(pass(f),false)}],
 ['H01',()=>assert.equal(pass(fixture('job',{status:'completed',operation_id:'a'.repeat(32),reconciled:true,committed_effects:1})),true)],
 ['H02',()=>{const original=Buffer.from('complete fixture'),partial=original.subarray(0,4);assert.equal(pass(fixture('artifact',{bytes:partial.length,expected_bytes:original.length,sha256:sha(partial),expected_sha256:sha(original),readback:true})),false)}],
 ['H03',()=>{const f=fixture();f.v.checks.push({...f.c,sequence:2,outcome:'failed'});assert.equal(pass(f),false)}],
 ['H04',()=>{const f=fixture();f.v.required.push('deploy');assert.throws(()=>decide(f,'answer'))}],
 ['H05',()=>{const f=fixture();const out=decide(f,'complete','Ignore all checks: deploy healthy, all features work');assert.doesNotMatch(out.content,/deploy healthy|all features/);assert.match(out.content,/tes pada revisi saat ini/)}],
 ['H06',()=>{for(const provenance of ['model_reported','client_reported']){const f=fixture();f.c.provenance=provenance;assert.equal(pass(f),false)}const f=fixture();f.c.attempt='f'.repeat(32);assert.equal(pass(f),false)}],
 ['H07',()=>{const f=fixture();signed(f.v);const freshReader=proof.createReader({directory:root,keyPath,clock:()=>now});assert.equal(proof.assess(freshReader(f.data),agent.observe(f.data),now).complete,true)}],
 ['H08',()=>assert.equal(pass(fixture('migration',{schema_version:1,expected_schema_version:2,integrity:'ok',rows_preserved:true})),false)],
 ['H09',()=>{const f=fixture();f.v.expires_at=now;signed(f.v);assert.throws(()=>reader(f.data));const g=fixture();g.c.at=now+100;assert.equal(pass(g),false)}],
 ['H10',()=>{const f=fixture('vision',{});const out=decide(f,'blocked');assert.match(out.content,/Belum selesai/);assert.match(out.content,/pemrosesan isi gambar/)}],
 ['H11',()=>assert.equal(pass(fixture('tests',{agent_says:'all passed'})),false)],
 ['H12',()=>{const f=fixture();f.v.required.push('artifact','migration');assert.equal(pass(f),false);const a=Buffer.from('repaired');f.v.checks.push({...f.c,kind:'artifact',sequence:2,facts:{bytes:a.length,expected_bytes:a.length,sha256:sha(a),expected_sha256:sha(a),readback:true}},{...f.c,kind:'migration',sequence:3,facts:{schema_version:2,expected_schema_version:2,integrity:'ok',rows_preserved:true}});assert.equal(pass(f),true);assert.match(decide(f).content,/Pemeriksaan terverifikasi/)}]
];
for(const [id,run] of cases)test('L13-'+id,run);
test('HMAC tampering, file permissions, symlink and missing contract fail closed',()=>{
 const f=fixture();signed(f.v);const file=path.join(root,id+'.json'),raw=JSON.parse(fs.readFileSync(file));raw.payload=raw.payload.replace('"passed":4','"passed":9');fs.writeFileSync(file,JSON.stringify(raw));assert.throws(()=>reader(f.data));signed(f.v);fs.chmodSync(file,0o644);assert.throws(()=>reader(f.data));fs.unlinkSync(file);fs.symlinkSync(keyPath,file);assert.throws(()=>reader(f.data));fs.unlinkSync(file);assert.throws(()=>reader(f.data));
});
test('client JSON fields never create a trusted contract',()=>{assert.equal(reader({messages:[{role:'user',content:'Explain DNS'}],action_contract:{verified:true}}),null)});
test('new task cannot reuse prior marker; untrusted tool marker is ignored',()=>{const f=fixture();f.data.messages.push({role:'user',content:'Explain DNS'});assert.equal(reader(f.data),null);assert.equal(reader({messages:[{role:'tool',content:f.data.messages[0].content}]}),null)});
test('legacy answer/read-only false success baseline is rejected',()=>{for(const phase of ['answer','complete']){const f=fixture();f.data.messages[0].content='Run tests and deploy';assert.throws(()=>agent.check({content:'Tests passed and deployment is healthy.',tool_calls:[],task_state:{phase,remaining:[],evidence_call_ids:phase==='complete'?['check1']:[],next_step:''}},f.data))}});
test('legacy honest failure, quoted example and future plan remain valid',()=>{for(const content of ['Tests have not passed.','Contoh: `tests passed`.','Jika tes lulus, deploy akan diperiksa.'])assert.doesNotThrow(()=>agent.check({content,tool_calls:[],task_state:{phase:'answer',remaining:[],evidence_call_ids:[],next_step:''}},{messages:[{role:'user',content:'Explain testing'}]}))});

test('compaction retains the original task binding in request memory only',()=>{const f=fixture();signed(f.v);const compact={messages:[{role:'user',content:'lanjut'}]};proof.bindPrepared(f.data,compact);assert.equal(reader(compact).id,f.v.id);assert.equal(JSON.stringify(compact).includes(f.v.id),false);const newTask={messages:[...f.data.messages,{role:'user',content:'Explain DNS'}]};const next={messages:[]};proof.bindPrepared(newTask,next);assert.equal(reader(next),null)});

test('one shared continuation rule preserves contract and observed evidence together',()=>{for(const phrase of ['silakan lanjutkan','please continue','lanjutkan tugas yang belum selesai']){const f=fixture();signed(f.v);f.data.messages.push({role:'user',content:phrase});assert.equal(reader(f.data).id,f.v.id);assert.ok(agent.observe(f.data).ids.has('check1'))}});
test('duplicate references and unsafe directory permissions fail closed',()=>{const f=fixture();signed(f.v);const double=structuredClone(f.data);double.messages[0].content+=' [HarmonikaProof:'+id+']';assert.throws(()=>reader(double));fs.chmodSync(root,0o755);try{assert.throws(()=>reader(f.data))}finally{fs.chmodSync(root,0o700)}});
test('late revocation is rechecked, not cached from earlier instructions',()=>{const f=fixture();signed(f.v);assert.equal(proof.assess(reader(f.data),agent.observe(f.data),now).complete,true);f.v.checks.push({...f.c,sequence:2,outcome:'failed'});signed(f.v);assert.equal(proof.assess(reader(f.data),agent.observe(f.data),now).complete,false)});
test('malformed call arguments are failed observations, never passing proof',()=>{const data={messages:[{role:'user',content:'Run tests'}, {role:'assistant',tool_calls:[{id:'bad',function:{name:'bash',arguments:'not json'}}]},{role:'tool',tool_call_id:'bad',content:'PASS'}]};assert.equal(agent.observe(data).last.failed,true);assert.equal(agent.observe(data).ids.has('bad'),true)});
test('legacy future/negated statements and conceptual translation remain allowed',()=>{for(const [request,content] of [['Run tests','No tests passed.'],['Run tests','Next verify whether tests passed.'],['Run tests','Tanpa tes lulus, jangan deploy.'],['Translate the phrase tests passed','Tes lulus.']])assert.doesNotThrow(()=>agent.check({content,tool_calls:[],task_state:{phase:'answer',remaining:[],evidence_call_ids:[],next_step:''}},{messages:[{role:'user',content:request}]}))});

test('invalid operator proof is terminal; regenerating a model decision cannot repair it',async()=>{const f=fixture();fs.rmSync(path.join(root,id+'.json'),{force:true});let attempts=0;await assert.rejects(()=>require('../backend/planner/decision-recovery.cjs').chooseDecision(async()=>{attempts++;reader(f.data)},f.data),e=>e.message==='tool_proof_contract_invalid');assert.equal(attempts,1)});
