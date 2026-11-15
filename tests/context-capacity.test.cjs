const {test}=require('node:test');const assert=require('node:assert/strict');
const policy=require('../backend/planner/context-capacity.cjs');
const {compactPayload}=require('../backend/planner/planner-context');
const {project}=require('../backend/visual_quality/context_window.cjs');
test('profile selects a real model and separates token capacity from character transport',()=>{
 const p=policy.read();assert.equal(p.context_window_tokens,872000);assert.ok(p.auto_compact_tokens<p.context_window_tokens);
 assert.ok(policy.args(p).includes('model_context_window=872000'));
});
test('large valid evidence remains intact through both projections',()=>{
 const input={messages:[{role:'user',content:'Read the fixture'},{role:'assistant',content:null,tool_calls:[{id:'a',type:'function',function:{name:'read_code',arguments:'{"path":"fixture.txt"}'}}]},{role:'tool',tool_call_id:'a',content:'synthetic source '.repeat(30000)},{role:'user',content:'Keep every record; answer from the middle'}],tools:[]};
 const before=JSON.stringify(input);const options={maxChars:policy.read().transport_soft_chars};
 assert.equal(project(input,options),input);assert.deepEqual(compactPayload(input,options).data,input);assert.equal(JSON.stringify(input),before);
});
test('larger context preserves the data firewall and rejects oversized bytes',()=>{
 const options={maxChars:policy.read().transport_soft_chars};
 const input={messages:[{role:'user',content:'password="synthetic-private-credential"'}]};
 assert.ok(!JSON.stringify(compactPayload(input,options)).includes('synthetic-private-credential'));
 assert.throws(()=>compactPayload({messages:[{role:'user',content:'界'.repeat(3000000)}]},options),/tool_input_too_large/);
 assert.throws(()=>compactPayload(input,{maxChars:99999999}),/context_policy_invalid/);
});
