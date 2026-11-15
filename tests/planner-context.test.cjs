'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {compactPayload, BUDGET} = require('../backend/planner/planner-context');
const {scrub, scrubText, assertNoSecrets, assertToolRequest} = require('../backend/planner/secret-firewall.cjs');
const tools = [{type:'function',function:{name:'read_code',description:'Keep all schema details',parameters:{type:'object',properties:{path:{type:'string'}},required:['path']}}}];
const call = (id, name='read_code') => ({role:'assistant',content:null,tool_calls:[{id,type:'function',function:{name,arguments:'{"path":"src/app.js"}'}}]});
const result = (id, content) => ({role:'tool',tool_call_id:id,content});
test('preserves instructions in order, latest user and exact schemas without mutating input', () => {
  const messages = [{role:'system',content:'policy '.repeat(6000)},{role:'user',content:'old'},{role:'developer',content:'Never deploy without verification'},{role:'user',content:'current '.repeat(9000)}];
  const input = {messages,tools}; const before = JSON.stringify(input);
  const out = compactPayload(input).data;
  assert.equal(JSON.stringify(input), before); assert.deepEqual(out.messages,messages); assert.deepEqual(out.tools,tools);
});
test('deduplicates old read output, keeps paired calls/results and full latest turn', () => {
  const body = 'read-only source\n'.repeat(2000);
  const input = {messages:[{role:'user',content:'read'},call('one'),result('one',body),{role:'user',content:'read again'},call('two'),result('two',body),{role:'user',content:'now answer'}],tools};
  const out = compactPayload(input);
  assert.match(out.data.messages[2].content,/Duplicate earlier/);
  assert.match(out.data.messages[5].content,/Earlier tool output abbreviated/);
  assert.equal(out.data.messages[1].tool_calls[0].id,out.data.messages[2].tool_call_id);
  assert.match(out.notes.join(' '),/duplicates_removed=1/);
});
test('preserves command, mutation, verification and error evidence byte for byte', () => {
  const evidence = 'verification failed; do not retry mutation\n'.repeat(2000);
  for (const tool of ['ssh_exec','write_file','git_commit','test_run','health_check','audit','recovery']) {
    const input={messages:[{role:'user',content:'action'},call('one',tool),result('one',evidence),{role:'user',content:'next'}],tools};
    assert.equal(compactPayload(input).data.messages[2].content,evidence);
  }
});
test('drops whole older turns, never separates parallel tool pairs', () => {
  const messages=[{role:'system',content:'preserve'}];
  for(let i=0;i<40;i++) messages.push({role:'user',content:'old '.repeat(2000)},call('id'+i),result('id'+i,'output '.repeat(1000)));
  messages.push({role:'user',content:'latest'});
  const out=compactPayload({messages,tools}).data;
  assert.ok(JSON.stringify(out).length<=BUDGET); assert.equal(out.messages.at(-1).content,'latest');
  assert.equal(out.messages.filter(x=>x.role==='tool').length,out.messages.filter(x=>x.tool_calls).length);
  for (const m of out.messages.filter(x=>x.role==='tool')) assert.ok(out.messages.some(x=>x.tool_calls?.some(c=>c.id===m.tool_call_id)));
});
test('unfit critical content fails explicitly instead of clipping user/instructions/tools', () => {
  for (const role of ['system','developer','user']) assert.throws(()=>compactPayload({messages:[{role,content:'x'.repeat(BUDGET)}],tools}),/tool_input_too_large/);
});
test('rejects orphan, duplicate, unfinished and mismatched tool history', () => {
  for (const messages of [[result('x','orphan')],[call('a')],[call('a'),result('b','bad')],[call('a'),result('a','ok'),call('a'),result('a','bad')],[call('a'),{role:'user',content:'new'},result('a','bad')]]) assert.throws(()=>compactPayload({messages,tools}),/invalid_tool_history/);
});
test('parallel tool calls stay valid and complete', () => {
  const a=call('a');a.tool_calls.push(call('b').tool_calls[0]);
  const input={messages:[{role:'user',content:'read two'},a,result('b','two'),result('a','one')],tools};
  assert.deepEqual(compactPayload(input).data,input);
});
test('firewall removes secrets from SSH/MCP JSON, shell output and pasted text without raw logs', () => {
  const value='synthetic-credential-value-12345';
  for(const text of ['HARMONIKA_API_KEY='+value, 'password="'+value+'"', '"client_secret": "'+value+'"','Authorization: Bearer '+value,'Cookie: session='+value,'curl --password '+value,'https://name:'+value+'@example.invalid/']) assert.ok(!scrubText(text).includes(value),text.split(value)[0]);
  const input={messages:[{role:'tool',tool_call_id:'a',content:JSON.stringify({password:value,ok:true})}],tools};
  input.messages.unshift(call('a','ssh_exec'));
  const output=compactPayload(input).data;
  assert.ok(!JSON.stringify(output).includes(value));assert.equal(JSON.parse(output.messages[1].content).ok,true);
  assert.throws(()=>assertNoSecrets({password:value}),/tool_secret_reference_required/);
  assert.deepEqual(JSON.parse(JSON.stringify(scrub({credential_ref:'private-ref:alias',ok:true}))),{credential_ref:'private-ref:alias',ok:true});
});
test('decision context omits binary media and retains honest tool instruction', () => {
  const out=compactPayload({messages:[{role:'user',content:[{type:'image_url',image_url:{url:'data:image/png;base64,'+'a'.repeat(1000)}}]}],tools});
  assert.ok(!JSON.stringify(out.data).includes('base64'));assert.match(out.data.messages[0].content[0].text,/not visual analysis/);
});
test('bounded complexity, provider definitions and nonsecret values', () => {
  assertNoSecrets({port:22,host:'staging.example.invalid',credential_ref:'private-ref:staging'});
  const object={};object.self=object;assert.throws(()=>scrub(object),/tool_input_complexity/);
  assert.throws(()=>compactPayload({messages:Array.from({length:4097},()=>({role:'user',content:'x'}))}),/tool_input_too_large/);
});
test('generated secret reads and placeholders cannot reach an executor', () => {
  for (const args of [{path:'.env'}, {command:'cat .env'}, {command:'printenv'}, {path:'/etc/shadow'}, {password:'[REDACTED_SECRET]'}]) assert.throws(()=>assertToolRequest('ssh_exec',args),/tool_secret_reference_required/);
  assertToolRequest('read_file',{path:'.env.example'});
  assertToolRequest('ssh_exec',{alias:'staging',command:'apt-cache policy nginx'});
});
test('writing source code with environment references needs no raw credential', () => {
  for (const content of ['const apiKey = process.env.HARMONIKA_API_KEY;', 'api_key=os.environ["SERVICE_KEY"]', "api_key = getenv('HARMONIKA_API_KEY');", "password = '';", 'API_KEY=${SERVICE_KEY}']) assertToolRequest('write_file',{path:'src/client.py',content});
});
test('Cline command arrays and nested file descriptors obey the same private-reference firewall', () => {
  for (const args of [{commands:['cat /etc/shadow']},{commands:['printenv']},{commands:[{command:'cat .env'}]},{files:[{path:'/home/operator/.ssh/id_ed25519'}]}]) {
    assert.throws(()=>assertToolRequest('run_commands',args),/tool_secret_reference_required/);
  }
  assertToolRequest('run_commands',{commands:["python3 -c \"import os; print(bool(os.environ.get('LADUNI_ADMIN_PASSWORD')))\""]});
  assertToolRequest('read_files',{files:[{path:'src/config.js'},{path:'.env.example'}]});
});
