'use strict';
// Run against a privately staged planner source with its real AJV and firewall.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {createRequire} = require('node:module');
const {EventEmitter} = require('node:events');
const candidate = process.env.HARMONIKA_TEST_PLANNER_SOURCE;
const base = process.env.HARMONIKA_TEST_PLANNER_BASE || '/opt/harmonika-ai/api/internal-tool-planner.js';
const tools = [{type:'function', function:{name:'run_commands',parameters:{type:'object',properties:{commands:{type:'array',items:{type:'string'}}},required:['commands'],additionalProperties:false}}}];
function protocolFixture(value){
  if(!value||typeof value!=='object'||value.task_state)return value;
  return {...value,task_state:{phase:value.tool_calls?.length?'execute':'answer',remaining:value.tool_calls?.length?['verify actual result']:[],evidence_call_ids:[],next_step:''}};
}
async function run(outputs, options = {}) {
  const logs = [], schemas = [], prompts = [], longCalls = [], timers = [], response = {chunks:[],status:null};
  let spawns = 0, signals = 0, clock = 0;
  const realRequire = createRequire(base);
  const processStub = {spawn(_bin, argv) {
    const child = new EventEmitter(); child.stderr = new EventEmitter(); child.stdin = new EventEmitter();
    child.stdin.end = prompt => {
      prompts.push(prompt);
      const file = argv[argv.indexOf('-o') + 1];
      schemas.push(JSON.parse(fs.readFileSync(path.join(path.dirname(file), 'schema.json'))));
      if(options.hangShort){spawns++;setTimeout(()=>child.emit('close',0,null),40);return;}
      const next = options.harmonika_translation === true ? outputs[spawns++] : protocolFixture(outputs[spawns++]);
      if (next === undefined) throw Error('unexpected_generation');
      fs.writeFileSync(file, typeof next === 'string' ? next : JSON.stringify(next));
      queueMicrotask(() => child.emit('close', 0, null));
    };
    return child;
  }};
  const module = {exports:{}};
  const safeFs = {...fs, readFileSync(file, ...args) {
    return file === '/etc/harmonika-ai/internal-agent.token' ? 'synthetic-agent-secret' : fs.readFileSync(file, ...args);
  }};
  const scope = {module, exports:module.exports, JSON, SyntaxError, require(name) {
    if (name === 'child_process') return processStub;
    if (name === 'fs') return safeFs;
    if (name === './decision-recovery.cjs') {
      const recovery=require('../backend/planner/decision-recovery.cjs');
      return options.deadlineProbe?{...recovery,chooseDecision:(plan,data,log)=>recovery.chooseDecision(plan,data,log,async ms=>{clock+=ms;},{now:()=>clock})}:recovery;
    }
    if (name === './policy-errors.cjs') return require('../backend/planner/policy-errors.cjs');
    if (name === './translation-mode.cjs') return require('../backend/planner/translation-mode.cjs');
    if (name === './secret-firewall.cjs') return require('../backend/planner/secret-firewall.cjs');
    if (name === './agent-controller.cjs') {
      const controller=require('../backend/planner/agent-controller.cjs');
      return options.scopeAdmissionProbe?{...controller,extendSchema(schema,data){if(options.rejectScope)throw Error('tool_scope_denied');return controller.extendSchema(schema,data);}}:controller;
    }
    if (name === './long-decision.cjs') {
      const transport = require('../backend/planner/long-decision.cjs');
      return options.longOutput === undefined ? transport : {...transport,async run(input){longCalls.push(input);if(options.longHold)throw Error('tool_engine_timeout');input.onClose?.();if(options.longOutputLimitOnce&&longCalls.length===1)throw Object.assign(Error('tool_engine_output_limit'),{decisionUnexecuted:true,transportClosed:true});if(options.longMalformedAfterOverflow&&longCalls.length===2)return 'broken';if(options.longTimeoutOnce&&longCalls.length===1){if(options.deadlineProbe)clock=180400;throw Object.assign(Error('tool_engine_timeout'),{decisionUnexecuted:true,transportClosed:true});}return JSON.stringify(protocolFixture(options.longOutput));}};
    }
    return realRequire(name);
  }, console:{log:line => logs.push(JSON.parse(line)),warn:line => logs.push(JSON.parse(line))},setTimeout:(fn,ms,...args)=>{timers.push(ms);return setTimeout(fn,options.hangShort&&ms===60000?5:ms,...args);},clearTimeout,Buffer,process:{kill(){signals++;throw Error('unexpected_signal');}}};
  vm.runInNewContext(fs.readFileSync(candidate,'utf8'), scope, {filename:base});
  const req = {headers:{'x-harmonika-agent-secret':options.invalidInternalSecret?'invalid-secret':'synthetic-agent-secret'},socket:{remoteAddress:'127.0.0.1'}};
  const res = {writeHead(status){response.status=status;},write(text){response.chunks.push(text);},end(text){if(text)response.chunks.push(text);}};
  const data = {messages:[{role:'user',content:'Synthetic fixture: check a private reference without exposing secrets.'}], tools,parallel_tool_calls:false,stream:false,...options};
  if(options.scopeAdmissionProbe){
    const statuses=[];let denied=null;
    for(let i=0;i<12;i++){await module.exports.handle(req,res,data);statuses.push(response.status);if(i===0)denied=JSON.parse(response.chunks.join(''));}
    options.rejectScope=false;response.chunks=[];await module.exports.handle(req,res,data);
    return {statuses,status:response.status,spawns,denied};
  }
  if(options.admissionProbe){
    async function invoke(){let status,body='';await module.exports.handle(req,{writeHead(s){status=s;},write(s){body+=s;},end(s){if(s)body+=s;}},data);return {status,json:JSON.parse(body)};}
    const initial=await Promise.all(Array.from({length:8},()=>invoke()));
    const busyBefore=await invoke();longCalls[0].onClose();longCalls[0].onClose();
    const postOneClose=await invoke(),stillBusy=await invoke();
    for(const call of longCalls)call.onClose();options.longHold=false;
    const afterAllClosed=await invoke();
    return {initial,busyBefore,postOneClose,stillBusy,afterAllClosed,longCalls:longCalls.length,signals,logs};
  }
  await module.exports.handle(req, res, data);
  const body = response.chunks.join('');
  return {status:response.status,body,json:data.stream?null:JSON.parse(body),logs,schemas,prompts,spawns,longCalls,signals,timers};
}
const call = command => ({name:'run_commands',arguments:JSON.stringify({commands:[command]})});
test('repeated scope/schema rejections do not consume model admission slots', {skip:!candidate},async()=>{
 const out=await run([{content:'A valid next answer.',tool_calls:[]}],{scopeAdmissionProbe:true,rejectScope:true});
 assert.deepEqual(out.statuses,Array(12).fill(403));assert.equal(out.status,200);assert.equal(out.spawns,1);
});
test('real planner repairs invalid outer JSON and emits one valid completion', {skip:!candidate}, async () => {
  const out = await run(['broken', {content:'Reference is missing; configure it privately.',tool_calls:[]}]);
  assert.equal(out.status,200);assert.equal(out.spawns,2);
  assert.ok(out.logs.some(e => e.reason==='result_json'));
  assert.match(out.prompts[1],/DECISION FORMAT CORRECTION/);
  assert.equal(out.json.choices[0].finish_reason,'stop');
});
test('malformed multiline arguments are repaired before any client call is emitted', {skip:!candidate}, async () => {
  const bad = {content:'',tool_calls:[{name:'run_commands',arguments:'{"commands":["line one\nline two"]}'}]};
  const out = await run([bad,{content:'',tool_calls:[call('printf fixture')]}]);
  assert.equal(out.status,200);assert.equal(out.spawns,2);
  assert.ok(out.logs.some(e => e.reason==='arguments_json'));
  assert.equal(out.json.choices[0].message.tool_calls.length,1);
  assert.deepEqual(JSON.parse(out.json.choices[0].message.tool_calls[0].function.arguments),{commands:['printf fixture']});
});
test('nonparallel output rejected rather than silently executing a subset', {skip:!candidate}, async () => {
  const out = await run([{content:'',tool_calls:[call('printf one'),call('printf two')]},{content:'',tool_calls:[call('printf one')]}]);
  assert.equal(out.status,200);assert.equal(out.spawns,2);
  assert.equal(out.schemas[0].properties.tool_calls.maxItems,1);
  assert.equal(out.json.choices[0].message.tool_calls.length,1);
});
test('missing private reference is a normal successful result without a new shell call', {skip:!candidate}, async () => {
  const out = await run([{content:'The configured reference is absent; request private configuration.',tool_calls:[]}],{messages:[
    {role:'user',content:'Check whether the reference exists.'},
    {role:'assistant',content:null,tool_calls:[{id:'fixture',type:'function',function:call("python3 -c 'print(False)'")}]},
    {role:'tool',tool_call_id:'fixture',content:'private_password_reference_available=False'}]});
  assert.equal(out.status,200);assert.equal(out.spawns,1);
  assert.equal(out.json.choices[0].message.tool_calls,undefined);
  assert.ok(!JSON.stringify(out.logs).includes('private_password_reference_available'));
});
test('real argument schema and secret firewall failures remain terminal', {skip:!candidate}, async () => {
  for (const [args,code] of [[{commands:10},'invalid_tool_arguments'],[{commands:['cat /etc/shadow']},'tool_secret_reference_required']]) {
    const out = await run([{content:'',tool_calls:[{name:'run_commands',arguments:JSON.stringify(args)}]}]);
    assert.equal(out.spawns,1);assert.equal(out.json.error.code,code);
  }
});
test('dynamic header reference passes the real validator without regenerating or executing a tool', {skip:!candidate}, async () => {
  for(const command of ["node -e \"const headers={Authorization:'Bearer '+sessions[session]};\"",'const headers={Authorization:`Bearer ${sessions[session]}`};']){
    const out=await run([{content:'',tool_calls:[call(command)]}]);
    assert.equal(out.status,200);assert.equal(out.spawns,1);
    assert.equal(JSON.parse(out.json.choices[0].message.tool_calls[0].function.arguments).commands[0],command);
  }
});
test('format repair is bounded and diagnostics contain no invalid response text', {skip:!candidate}, async () => {
  const out = await run(['synthetic-sensitive-output','synthetic-sensitive-output']);
  assert.equal(out.spawns,2);assert.equal(out.json.error.code,'invalid_tool_response');
  assert.ok(!JSON.stringify(out.logs).includes('synthetic-sensitive-output'));
});
test('SSE starts only after a valid decision and contains a single repaired call', {skip:!candidate}, async () => {
  const out = await run(['invalid',{content:'',tool_calls:[call('printf fixture')]}],{stream:true});
  assert.equal(out.status,200);assert.equal(out.spawns,2);
  const frames=out.body.split('\n\n').filter(x => x.startsWith('data: {')).map(x => JSON.parse(x.slice(6)));
  assert.equal(frames.filter(x => x.choices[0].delta.tool_calls).length,1);
  assert.match(out.body,/data: \[DONE\]/);
});
test('validated SSE tool progress has no cosmetic typing delay before client execution', {skip:!candidate}, async()=>{
 const content='Synthetic current finding. '.repeat(45);
 const out=await run([{content,tool_calls:[call('printf fixture')]}],{stream:true});
 assert.equal(out.status,200);assert.ok(!out.timers.some(ms=>[12,16,22].includes(ms)));
 const frames=out.body.split('\n\n').filter(x=>x.startsWith('data: {')).map(x=>JSON.parse(x.slice(6)));
 assert.equal(frames.map(x=>x.choices[0].delta.content||'').join(''),content);
 assert.equal(frames.filter(x=>x.choices[0].delta.tool_calls).length,1);
 assert.ok(out.body.includes('data: [DONE]'));
});
test('interactive source-writer protocol uses the 60s decision cap without removing evidence', {skip:!candidate}, async()=>{
 const tools=['read_files','run_commands','editor'].map(name=>({type:'function',function:{name,parameters:{type:'object',properties:{}}}}));
 const messages=[{role:'user',content:'Jelaskan fungsi DNS tanpa eksekusi. '+('synthetic evidence '.repeat(70000))}];
 const out=await run([],{tools,messages,longOutput:{content:'DNS menerjemahkan nama domain.',tool_calls:[]}});
 assert.equal(out.status,200);assert.equal(out.longCalls.length,1);assert.equal(out.longCalls[0].timeoutMs,60000);
 assert.deepEqual(JSON.parse(out.longCalls[0].payload).messages,messages);
 assert.ok(out.logs.some(e=>e.event==='internal_tool_budget'&&e.request_ms===90000));
});
test('large intact client call/result history routes before spawning CLI exec', {skip:!candidate}, async () => {
  const evidence='synthetic evidence '.repeat(65000);
  const messages=[{role:'user',content:'Read the evidence and continue.'},
    {role:'assistant',content:null,tool_calls:[{id:'done',type:'function',function:call('printf fixture')}]},
    {role:'tool',tool_call_id:'done',content:evidence},{role:'user',content:'Now report the outcome.'}];
  const out=await run([],{messages,longOutput:{content:'Evidence retained.',tool_calls:[]}});
  assert.equal(out.status,200);assert.equal(out.spawns,0);assert.equal(out.longCalls.length,1);
  assert.deepEqual(JSON.parse(out.longCalls[0].payload).messages,messages);
  assert.equal(out.json.choices[0].finish_reason,'stop');
});
test('admission size failures are reported as context capacity, not invalid tool arguments', {skip:!candidate}, async () => {
  const out=await run([],{messages:[{role:'user',content:'x'.repeat(8388608)}]});
  assert.equal(out.status,413);assert.equal(out.json.error.code,'context_length_exceeded');
  assert.equal(out.spawns,0);
});
test('small source writer uses protocol while keeping schema validation and source guard', {skip:!candidate}, async()=>{
 const tools=[{type:'function',function:{name:'write_source',parameters:{type:'object',properties:{path:{type:'string'},content:{type:'string'}},required:['path','content'],additionalProperties:false}}}];
 const source="def login(value):\n return {'password':value}\n";
 const out=await run([],{tools,longOutput:{content:'',tool_calls:[{name:'write_source',arguments:JSON.stringify({path:'app.py',content:source})}]}});
 assert.equal(out.status,200);assert.equal(out.spawns,0);assert.equal(out.longCalls.length,1);
 assert.equal(JSON.parse(out.json.choices[0].message.tool_calls[0].function.arguments).content,source);
 const rejected=await run([],{tools,longOutput:{content:'',tool_calls:[{name:'write_source',arguments:JSON.stringify({path:'app.py',content:"def login():\n return {'password':'inline-secret'}\n"})}]}});
 assert.equal(rejected.status,400);assert.equal(rejected.json.error.code,'tool_secret_reference_required');assert.equal(rejected.longCalls.length,1);
});
test('confirmed unexecuted protocol timeout recovers one decision and emits one client call', {skip:!candidate}, async()=>{
 const writer={type:'function',function:{name:'write_srcfile',parameters:{type:'object',properties:{path:{type:'string'},content:{type:'string'}},required:['path','content'],additionalProperties:false}}};
 const output={content:'',tool_calls:[{name:'write_srcfile',arguments:JSON.stringify({path:'/tmp/synthetic/generated.py',content:'print(42)\n'})}]};
 const out=await run([],{tools:[writer],longOutput:output,longTimeoutOnce:true});
 assert.equal(out.status,200);assert.equal(out.longCalls.length,2);assert.equal(out.json.choices[0].message.tool_calls.length,1);assert.ok(out.logs.some(x=>x.event==='internal_tool_retry'&&x.code==='tool_engine_timeout'&&x.transport_closed===true));assert.equal(out.signals,0);
});
test('real planner forwards the remaining request deadline to a closed-transport retry', {skip:!candidate},async()=>{
 const writer={type:'function',function:{name:'write_srcfile',parameters:{type:'object',properties:{path:{type:'string'},content:{type:'string'}},required:['path','content'],additionalProperties:false}}};
 const out=await run([],{tools:[writer],deadlineProbe:true,longTimeoutOnce:true,longOutput:{content:'',tool_calls:[{name:'write_srcfile',arguments:JSON.stringify({path:'/tmp/synthetic/generated.py',content:'print(42)\n'})}]}});
 assert.equal(out.status,200);assert.deepEqual(out.longCalls.map(x=>x.timeoutMs),[180000,59200]);
 assert.equal(out.json.choices[0].message.tool_calls.length,1);assert.equal(out.signals,0);
});
test('timed-out protocol children hold eight admission slots until actual close, releasing once', {skip:!candidate},async()=>{
 const writer={type:'function',function:{name:'write_srcfile',parameters:{type:'object',properties:{path:{type:'string'},content:{type:'string'}},required:['path','content'],additionalProperties:false}}};
 const out=await run([],{tools:[writer],longOutput:{content:'No further action requested.',tool_calls:[]},longHold:true,admissionProbe:true});
 assert.ok(out.initial.every(x=>x.status===503&&x.json.error.code==='tool_engine_timeout'));assert.equal(out.busyBefore.status,429);assert.equal(out.postOneClose.status,503);assert.equal(out.stillBusy.status,429);assert.equal(out.afterAllClosed.status,200);assert.equal(out.longCalls,10);assert.equal(out.signals,0);
});
test('short decision deadline sends no signal and ignores a late CLI result', {skip:!candidate}, async()=>{
 const out=await run([],{hangShort:true});
 assert.equal(out.json.error.code,'tool_engine_timeout');assert.equal(out.signals,0);assert.equal(out.spawns,1);
 await new Promise(resolve=>setTimeout(resolve,60));
 assert.ok(!out.logs.some(row=>row.event==='internal_tool_decision'&&row.status==='ok'));
 assert.ok(!out.logs.some(row=>row.event==='internal_tool_format_repair'));
});

test('real planner keeps overflow caps after a subsequent format repair', {skip:!candidate}, async()=>{
 const out=await run([],{messages:[{role:'user',content:'synthetic reference '.repeat(60000)}],longOutput:{content:'Concise answer',tool_calls:[]},longOutputLimitOnce:true,longMalformedAfterOverflow:true});
 assert.equal(out.status,200);assert.equal(out.longCalls.length,3);assert.equal(out.spawns,0);assert.equal(out.signals,0);
 for(const call of out.longCalls.slice(1)){
  assert.equal(call.schema.properties.tool_calls.maxItems,1);
  assert.equal(call.schema.properties.content.maxLength,2000);
  assert.equal(call.schema.properties.tool_calls.items.properties.arguments.maxLength,12000);
 }
 assert.match(out.longCalls[2].instructions,/DECISION FORMAT CORRECTION/);
 assert.match(out.longCalls[2].instructions,/OUTPUT BUDGET CORRECTION/);
});

const translationInput = {harmonika_translation:true, stream:false, tool_choice:'none', tools:[], messages:[
  {role:'system',content:'Translate faithfully and return the requested JSON result.'},
  {role:'user',content:JSON.stringify({text:'OpenAI, Codex and Google remain named.',target:'id'})}
]};
test('private translation preserves names without granting agent task or tools', {skip:!candidate}, async()=>{
  const result=JSON.stringify({translated_text:'OpenAI, Codex dan Google tetap disebut.',detected_language:'en',needs_review:false});
  const out=await run([{content:result,tool_calls:[]}],translationInput);
  assert.equal(out.status,200);assert.equal(out.spawns,1);
  assert.equal(out.json.choices[0].message.content,result);
  assert.equal(out.schemas[0].properties.tool_calls.maxItems,0);
  assert.ok(!out.schemas[0].required.includes('task_state'));
  assert.ok(!out.prompts[0].includes('NEVER say you are Codex'));
  assert.ok(!out.logs.some(e=>e.event==='agent_task_state'));
});
test('translation mode requires internal authentication before any inference', {skip:!candidate},async()=>{
  const out=await run([],{...translationInput,invalidInternalSecret:true});
  assert.equal(out.status,403);assert.equal(out.spawns,0);
});
test('translation cannot declare executable client tools', {skip:!candidate},async()=>{
  const out=await run([],{...translationInput,tools});
  assert.equal(out.status,400);assert.equal(out.spawns,0);
  assert.equal(out.json.error.code,'translation_invalid_request');
});

test('real firewall and scope failures carry precise shared diagnostics without widening authorization', {skip:!candidate},async()=>{
 const secret=await run([{content:'',tool_calls:[call('cat /etc/shadow')]}]);
 assert.equal(secret.status,400);assert.equal(secret.json.error.code,'tool_secret_reference_required');assert.equal(secret.json.diagnostic.category,'credential_missing');assert.equal(secret.json.diagnostic.grants_permission,false);assert.equal(secret.spawns,1);
 const scope=await run([{content:'A normal answer.',tool_calls:[]}],{scopeAdmissionProbe:true,rejectScope:true});
 assert.deepEqual(scope.statuses,Array(12).fill(403));assert.equal(scope.spawns,1);assert.equal(scope.denied.diagnostic.category,'scope_denied');assert.equal(scope.denied.diagnostic.grants_permission,false);
});
