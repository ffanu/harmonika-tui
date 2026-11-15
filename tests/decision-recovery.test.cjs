'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const r = require('../backend/planner/decision-recovery.cjs');
test('repairs malformed JSON once and preserves completed client tool history', async () => {
  const data = {messages:[{role:'tool', content:'private_password_reference_available=False', tool_call_id:'done'}]};
  const before = JSON.stringify(data), reasons = [], logs = [];
  const result = await r.chooseDecision(async (input, reason) => {
    assert.equal(input, data); reasons.push(reason);
    if (reasons.length === 1) return r.parseResult('broken JSON', x => x, input);
    return {content:'Reference is missing.', tool_calls:[]};
  }, data, e => logs.push(e));
  assert.equal(reasons.length, 2); assert.equal(reasons[1], 'result_json');
  assert.deepEqual(result.tool_calls, []); assert.equal(JSON.stringify(data), before);
  assert.ok(!JSON.stringify(logs).includes('private_password_reference'));
});
test('repeated malformed decision stops after two generations without replay', async () => {
  let attempts = 0;
  await assert.rejects(r.chooseDecision(async () => { attempts++; throw r.invalid('arguments_json'); }, {}), /invalid_tool_response/);
  assert.equal(attempts, 2);
});
test('secret, schema, auth, history, timeout and arbitrary errors never trigger format retry', async () => {
  for (const code of ['tool_secret_reference_required','invalid_tool_arguments','invalid_tool_history','tool_engine_timeout','authentication_error','unexpected']) {
    let attempts = 0;
    await assert.rejects(r.chooseDecision(async () => { attempts++; throw Error(code); }, {}), new RegExp(code));
    assert.equal(attempts, 1);
  }
});
test('combined outage and repair have a total budget of three generations', async () => {
  let attempts = 0;
  await assert.rejects(r.chooseDecision(async () => {
    attempts++; if (attempts === 2) throw r.invalid('decision_contract');
    throw Error('tool_engine_unavailable');
  }, {}, () => {}, async () => {}), /tool_engine_unavailable/);
  assert.equal(attempts, 3);
});
test('arguments SyntaxError is classified but schema and firewall rejection are preserved', () => {
  const raw=JSON.stringify({content:'',tool_calls:[]});
  assert.throws(() => r.parseResult(raw, () => JSON.parse('{'), {}), e => e.message === 'invalid_tool_response' && e.decisionReason === 'arguments_json');
  for (const code of ['invalid_tool_arguments','tool_secret_reference_required']) assert.throws(() => r.parseResult(raw, () => {throw Error(code);}, {}), new RegExp(code));
  assert.throws(() => r.parseResult(raw, () => {throw Error('empty_tool_response');}, {}), e => e.decisionReason === 'empty_decision');
  for(const value of [null,{}, {content:'',tool_calls:[null]}, {content:'',tool_calls:[{name:'run_commands',arguments:{}}]}]) assert.throws(()=>r.parseResult(JSON.stringify(value),()=>{},{}),e=>e.decisionReason==='decision_contract');
});
test('output schema enforces named tool, none and nonparallel constraints without dropping calls', () => {
  const schema = {properties:{tool_calls:{type:'array',items:{properties:{name:{enum:['one','two']}}}}}};
  const tools = [{},{}], before = JSON.stringify(schema);
  assert.equal(r.constrainSchema(schema,{tools,parallel_tool_calls:false}).properties.tool_calls.maxItems,1);
  assert.equal(r.constrainSchema(schema,{tools,tool_choice:'none'}).properties.tool_calls.maxItems,0);
  assert.equal(r.constrainSchema(schema,{tools:[]}).properties.tool_calls.maxItems,0);
  const named = r.constrainSchema(schema,{tools,tool_choice:{type:'function',function:{name:'two'}}}).properties.tool_calls;
  assert.equal(named.minItems,1); assert.deepEqual(named.items.properties.name.enum,['two']);
  assert.equal(JSON.stringify(schema),before);
});
test('diagnostic and corrective instruction cannot include raw provider output', () => {
  const error = r.invalid('raw secret synthetic-value');
  assert.equal(error.decisionReason,'decision_contract');
  assert.equal(r.repairInstruction('raw secret synthetic-value'),'');
  assert.match(r.repairInstruction('result_json'),/without repeating completed actions/);
});
test('confirmed closed unexecuted timeout regenerates at most once and never changes history',async()=>{
 const data={messages:[{role:'tool',tool_call_id:'already-done',content:'synthetic completed action'}]},before=JSON.stringify(data),logs=[];let attempts=0;
 const result=await r.chooseDecision(async input=>{assert.equal(input,data);if(++attempts===1){const e=Error('tool_engine_timeout');e.decisionUnexecuted=true;e.transportClosed=true;throw r.transportError(e);}return {content:'',tool_calls:[{name:'next_declared_tool',arguments:'{}'}]};},data,e=>logs.push(e),async()=>{});
 assert.equal(attempts,2);assert.equal(result.tool_calls.length,1);assert.equal(JSON.stringify(data),before);assert.equal(logs[0].decision_unexecuted,true);assert.ok(!JSON.stringify(logs).includes('synthetic completed action'));
});
test('incomplete timeout proofs never retry, even if one marker is present',async()=>{
 for(const fields of [{decisionUnexecuted:true},{transportClosed:true},{decisionUnexecuted:'true',transportClosed:true}]){
  let attempts=0;await assert.rejects(r.chooseDecision(async()=>{attempts++;throw r.transportError(Object.assign(Error('tool_engine_timeout'),fields));},{},()=>{},async()=>{}),/tool_engine_timeout/);assert.equal(attempts,1);
 }
});
test('repeated confirmed timeout stops after two unexecuted generations',async()=>{
 let attempts=0;await assert.rejects(r.chooseDecision(async()=>{attempts++;throw Object.assign(Error('tool_engine_timeout'),{decisionUnexecuted:true,transportClosed:true});},{},()=>{},async()=>{}),/tool_engine_timeout/);assert.equal(attempts,2);
});
test('retry shares one 240-second budget below the client header deadline',async()=>{
 let elapsed=0;const budgets=[];
 const result=await r.chooseDecision(async(data,repair,attempt)=>{
  budgets.push(attempt?.timeoutMs);
  if(budgets.length===1){elapsed=180400;throw Object.assign(Error('tool_engine_timeout'),{decisionUnexecuted:true,transportClosed:true});}
  return {content:'Verified next decision',tool_calls:[]};
 },{},()=>{},async ms=>{elapsed+=ms;},{now:()=>elapsed});
 assert.deepEqual(budgets,[180000,59200]);assert.equal(result.content,'Verified next decision');
});
test('exhausted request deadline does not start another provider or replay a tool',async()=>{
 let elapsed=0,calls=0,waits=0;
 await assert.rejects(r.chooseDecision(async()=>{calls++;elapsed=240001;throw r.invalid('result_json');},{},()=>{},async()=>{waits++;},{now:()=>elapsed}),/tool_engine_timeout/);
 assert.equal(calls,1);assert.equal(waits,0);
});
test('format repair and unavailable retries also consume the same request budget',async()=>{
 let elapsed=0;const budgets=[];
 await assert.rejects(r.chooseDecision(async(data,repair,attempt)=>{
  budgets.push(attempt?.timeoutMs);elapsed+=100000;
  if(budgets.length===1)throw r.invalid('result_json');
  throw Error('tool_engine_unavailable');
 },{},()=>{},async ms=>{elapsed+=ms;},{now:()=>elapsed}),/tool_engine_timeout/);
 assert.deepEqual(budgets,[180000,140000,39600]);
});
test('deadline expiry preserves terminal auth, history, schema and firewall errors',async()=>{
 for(const code of ['tool_secret_reference_required','invalid_tool_history','tool_input_too_large','invalid_tool_arguments','authentication_error']){
  let elapsed=0,calls=0;
  await assert.rejects(r.chooseDecision(async()=>{calls++;elapsed=240001;throw Error(code);},{},()=>{},async()=>{throw Error('unexpected_retry');},{now:()=>elapsed}),e=>e.message===code);
  assert.equal(calls,1);
 }
});
test('closed unexecuted output overflow gets one smaller decision, preserving history',async()=>{
 const data={messages:[{role:'tool',tool_call_id:'done',content:'synthetic effect completed'}]},before=JSON.stringify(data),reasons=[];
 const result=await r.chooseDecision(async(input,repair)=>{assert.equal(input,data);reasons.push(repair);if(reasons.length===1)throw r.transportError(Object.assign(Error('tool_engine_output_limit'),{decisionUnexecuted:true,transportClosed:true}));return {content:'A concise result',tool_calls:[]};},data,()=>{},async()=>{});
 assert.deepEqual(reasons,['','output_budget']);assert.equal(result.content,'A concise result');assert.equal(JSON.stringify(data),before);assert.match(r.repairInstruction('output_budget'),/Do not repeat/);
});
test('output overflow with incomplete close proofs remains terminal',async()=>{
 for(const fields of [{},{decisionUnexecuted:true},{transportClosed:true}]){let calls=0;await assert.rejects(r.chooseDecision(async()=>{calls++;throw r.transportError(Object.assign(Error('tool_engine_output_limit'),fields));},{},()=>{},async()=>{}),/tool_engine_output_limit/);assert.equal(calls,1)}
});
test('repeated proven overflow stops after one bounded recovery within total budget',async()=>{
 let calls=0;await assert.rejects(r.chooseDecision(async()=>{calls++;throw Object.assign(Error('tool_engine_output_limit'),{decisionUnexecuted:true,transportClosed:true});},{},()=>{},async()=>{}),/tool_engine_output_limit/);assert.equal(calls,2);
});
test('output repair narrows schema without changing caller choices or original schema',()=>{
 const schema={properties:{content:{type:'string'},tool_calls:{type:'array',items:{properties:{name:{enum:['write']},arguments:{type:'string'}}}},task_state:{properties:{remaining:{items:{type:'string'}},next_step:{type:'string'}}}}};const before=JSON.stringify(schema);const fixed=r.constrainSchema(schema,{tools:[{}],tool_choice:'required'},'output_budget');assert.equal(fixed.properties.tool_calls.maxItems,1);assert.equal(fixed.properties.tool_calls.minItems,1);assert.equal(fixed.properties.tool_calls.items.properties.arguments.maxLength,12000);assert.equal(fixed.properties.content.maxLength,2000);assert.equal(fixed.properties.task_state.properties.remaining.items.maxLength,500);assert.equal(JSON.stringify(schema),before);
});
test('bounded output correction never widens existing response string limits',()=>{
 const schema={properties:{content:{type:'string',maxLength:80},tool_calls:{items:{properties:{name:{enum:['one']},arguments:{type:'string',maxLength:256}}}},task_state:{properties:{remaining:{items:{maxLength:20}},next_step:{maxLength:30}}}}};const fixed=r.constrainSchema(schema,{tools:[{}]},'output_budget');assert.equal(fixed.properties.content.maxLength,80);assert.equal(fixed.properties.tool_calls.items.properties.arguments.maxLength,256);assert.equal(fixed.properties.task_state.properties.remaining.items.maxLength,20);assert.equal(fixed.properties.task_state.properties.next_step.maxLength,30);
});
test('output caps persist when a later malformed decision needs format repair',async()=>{
 const schema={properties:{content:{type:'string'},tool_calls:{items:{properties:{name:{enum:['one']},arguments:{type:'string'}}}}}};
 const attempts=[];
 await r.chooseDecision(async(data,repair,attempt)=>{
  attempts.push({repair,schema:r.constrainSchema(schema,data,repair,attempt)});
  if(attempts.length===1)throw Object.assign(Error('tool_engine_output_limit'),{decisionUnexecuted:true,transportClosed:true});
  if(attempts.length===2)throw r.invalid('result_json');
  return {content:'Concise recovery',tool_calls:[]};
 },{tools:[{}]},()=>{},async()=>{});
 assert.deepEqual(attempts.map(x=>x.repair),['','output_budget','result_json']);
 for(const index of [1,2]){
  assert.equal(attempts[index].schema.properties.tool_calls.maxItems,1);
  assert.equal(attempts[index].schema.properties.content.maxLength,2000);
  assert.equal(attempts[index].schema.properties.tool_calls.items.properties.arguments.maxLength,12000);
 }
});
