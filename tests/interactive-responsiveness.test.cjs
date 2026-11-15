'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const recovery=require('../backend/planner/decision-recovery.cjs');
const controller=require('../backend/planner/agent-controller.cjs');
const tools=['read_files','run_commands','editor'].map(name=>({type:'function',function:{name,parameters:{type:'object',properties:{}}}}));
test('interactive latency derives from actual capabilities, including clients with additional tools',()=>{
 const data={tools:[...tools,{type:'function',function:{name:'ask_question',parameters:{type:'object'}}}]};
 const before=JSON.stringify(data);
 assert.deepEqual(recovery.requestPolicy(data),{profile:'interactive_tools',requestMs:90000,attemptMs:60000});
 assert.equal(recovery.requestPolicy({tools:[...tools].reverse()}).profile,'interactive_tools');
 assert.equal(JSON.stringify(data),before);
 for(const invalid of [undefined,{}, {tools:'interactive'}, {tools:tools.slice(1)}, {tools:[...tools,tools[0]]},
   {tools:tools.map(t=>({...t,type:'other'}))}, {tools:tools.map(t=>({type:'function',function:{name:t.function.name}}))},
   {client:'cline',interactive:true,requestMs:1,tools:[]}]){
  assert.deepEqual(recovery.requestPolicy(invalid),{profile:'general',requestMs:240000,attemptMs:180000});
 }
});
test('interactive repair consumes one monotonic 90s budget without dropping large context or replaying tools',async()=>{
 let elapsed=0;const budgets=[],logs=[];
 const data={tools,messages:[{role:'user',content:'synthetic retained evidence '.repeat(55000)}]};
 const before=JSON.stringify(data);
 const out=await recovery.chooseDecision(async(input,repair,attempt)=>{
  assert.equal(input,data);budgets.push(attempt.timeoutMs);
  if(budgets.length===1){elapsed=60500;throw Object.assign(Error('tool_engine_timeout'),{decisionUnexecuted:true,transportClosed:true});}
  return {content:'A bounded next decision.',tool_calls:[]};
 },data,e=>logs.push(e),async ms=>{elapsed+=ms;},{now:()=>elapsed});
 assert.deepEqual(budgets,[60000,29100]);assert.equal(out.content,'A bounded next decision.');
 assert.equal(JSON.stringify(data),before);assert.ok(!JSON.stringify(logs).includes('synthetic retained evidence'));
 assert.equal(logs[0].request_ms,90000);
});
test('interactive timeouts never regenerate an unclosed transport or already executed operation',async()=>{
 for(const proof of [{},{decisionUnexecuted:true},{transportClosed:true}]){
  let calls=0;await assert.rejects(recovery.chooseDecision(async()=>{calls++;throw Object.assign(Error('tool_engine_timeout'),proof);},{tools},()=>{},async()=>{}),/tool_engine_timeout/);
  assert.equal(calls,1);
 }
});
test('interactive retries stop at deadline and terminal permissions/schema errors retain their reason',async()=>{
 for(const code of ['invalid_tool_arguments','invalid_tool_history','tool_secret_reference_required','authentication_error','tool_engine_unavailable']){
  let elapsed=0,calls=0;
  await assert.rejects(recovery.chooseDecision(async()=>{calls++;elapsed=90001;throw Error(code);},{tools},()=>{},async()=>{throw Error('unexpected_retry');},{now:()=>elapsed}),
   e=>e.message===(code==='tool_engine_unavailable'?'tool_engine_timeout':code));
  assert.equal(calls,1);
 }
});
test('interactive unavailable/format recovery stays bounded without overriding caller choices',async()=>{
 let elapsed=0;const budgets=[];const data={tools,parallel_tool_calls:false,tool_choice:'none'};
 await assert.rejects(recovery.chooseDecision(async(input,repair,attempt)=>{
  budgets.push(attempt.timeoutMs);assert.equal(input,data);elapsed+=40000;
  if(budgets.length===1)throw recovery.invalid('result_json');throw Error('tool_engine_unavailable');
 },data,()=>{},async ms=>{elapsed+=ms;},{now:()=>elapsed}),/tool_engine_timeout/);
 assert.deepEqual(budgets,[60000,50000,9600]);
});
test('progress guidance preserves history, schema and failure/proof semantics',()=>{
 const data={tools,parallel_tool_calls:false,messages:[{role:'user',content:'Jelaskan rencananya saja.'}]},before=JSON.stringify(data);
 const guide=controller.instructions(data);
 assert.match(guide,/Batch independent read-only/);assert.match(guide,/honor parallel_tool_calls=false/);
 assert.match(guide,/Do not repeat unchanged cautions/);assert.match(guide,/success:true only says a response arrived/);
 assert.match(guide,/Never suppress a current failure/);assert.equal(JSON.stringify(data),before);
});
test('Cline transport success cannot hide explicit failed process metadata in its command array',()=>{
 const data=(name,result)=>({messages:[{role:'user',content:'Run the fixture check.'},
  {role:'assistant',content:null,tool_calls:[{id:'fixture',type:'function',function:{name,arguments:'{}'}}]},
  {role:'tool',tool_call_id:'fixture',content:JSON.stringify(result)}]});
 const value=()=>({content:'Check complete.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:['fixture'],next_step:''}});
 for(const status of [{exit_code:1},{exitCode:'2'},{exit_status:-1},{status:'FAILED'}]){
  const input=data('run_commands',[{query:'fixture check',result:'synthetic process output',success:true,...status}]);
  assert.equal(controller.observe(input).last.failed,true);
  assert.throws(()=>controller.check(value(),input),e=>e.decisionReason==='unverified_completion');
 }
 for(const result of [[{query:'fixture check',result:'source example: Permission denied; exit_code:1',success:true,exit_code:0}],
  [{query:'fixture check',result:'snapshot not created',success:true}], [{exit_code:1}]]){
  assert.equal(controller.observe(data('run_commands',result)).last.failed,false);
 }
 assert.equal(controller.observe(data('read_files',[{query:'fixture',result:'document',success:true,exit_code:1}])).last.failed,false);
});
test('an explicit Cline editor write supports saving without fabricating test/deployment evidence',()=>{
 const data=args=>({messages:[{role:'user',content:'Simpan perbaikan fixture.'},
  {role:'assistant',content:null,tool_calls:[{id:'edit',type:'function',function:{name:'editor',arguments:JSON.stringify(args)}}]},
  {role:'tool',tool_call_id:'edit',content:JSON.stringify({success:true,path:'sum.js'})}]});
 const value=content=>({content,tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:['edit'],next_step:''}});
 const edited=data({path:'sum.js',new_text:'exports.sum=(a,b)=>a+b;'});
 assert.doesNotThrow(()=>controller.check(value('Perbaikan berhasil disimpan.'),edited));
 for(const claim of ['Perbaikan berhasil disimpan. Tests passed.','Perbaikan berhasil disimpan. Deployment is healthy.']){
  assert.throws(()=>controller.check(value(claim),edited),e=>e.decisionReason==='unverified_completion');
 }
 assert.throws(()=>controller.check(value('Perbaikan berhasil disimpan.'),data({path:'sum.js'})),e=>e.decisionReason==='unverified_completion');
});
