'use strict';
const {test,before}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os');
let guard,launch;
before(async()=>{guard=await import('../runtime/context-capacity/context_guard.mjs');launch=await import('../runtime/tui-control/launch.mjs')});
test('helper interpreter uses installer options and never shell-splits paths with spaces',()=>{
 assert.deepEqual(launch.helperCommand({python:'C:\\Program Files\\Python\\python.exe'},{},'win32'),['C:\\Program Files\\Python\\python.exe','-I']);
 assert.deepEqual(launch.helperCommand({},{HARMONIKA_PYTHON:'/opt/python/bin/python3'},'linux'),['/opt/python/bin/python3','-I']);
 assert.deepEqual(launch.helperCommand({},{},'win32'),['python','-I']);
 assert.throws(()=>launch.helperCommand({python:'python\nprivate'},{},'linux'),/invalid_python_command/);
});
test('context projection keeps operator capacity separate from transport characters',async()=>{
 const profile=guard.capacityProfile();assert.equal(profile.context_window_tokens,872000);assert.equal(profile.transport_soft_chars,2800000);
 const {project}=require('../runtime/context-capacity/context_window.cjs');
 const input={messages:[{role:'user',content:'fixture'},{role:'assistant',tool_calls:[{id:'a',type:'function',function:{name:'read',arguments:'{}'}}]},{role:'tool',tool_call_id:'a',content:'fixture data '.repeat(30000)},{role:'user',content:'retain middle evidence'}]};
 assert.equal(project(input,{maxChars:profile.transport_soft_chars}),input);
});
test('metadata excludes raw content and unknown role names; signed plugin files stay unchanged',async()=>{
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'harmonika-plugin-state-'));
 const before=await fs.readFile(path.join(__dirname,'../runtime/context-capacity/context_guard.mjs'));
 const input={messages:[{role:'system',content:'PRIVATE_SENTINEL'},{role:'PRIVATE_SENTINEL',content:'PRIVATE_SENTINEL'},{role:'user',content:'PRIVATE_SENTINEL'}],tools:[{description:'PRIVATE_SENTINEL'}]};
 try {
  const state=guard.projectionMechanics(input,input,123,()=> '2026-11-17T00:00:00Z');
  assert.ok(!JSON.stringify(state).includes('PRIVATE_SENTINEL'));
  await guard.recordMechanics(state,{XDG_STATE_HOME:directory});
  const target=path.join(directory,'harmonika-terminal/context-capacity/projection-state.json');
  assert.deepEqual(JSON.parse(await fs.readFile(target,'utf8')),state);
  if(process.platform!=='win32')assert.equal((await fs.stat(target)).mode&0o777,0o600);
  assert.deepEqual(await fs.readFile(path.join(__dirname,'../runtime/context-capacity/context_guard.mjs')),before);
 } finally {await fs.rm(directory,{recursive:true,force:true})}
});
test('guard scopes HTTP hook to Harmonika and disposes it without a network call',async()=>{
 let callback,disposed=false;
 const cleanup=await guard.default.setup({session:{hook:async(name,handler,options)=>{assert.equal(name,'http.request');assert.deepEqual(options,{providerID:'harmonika'});callback=handler;return {dispose:()=>{disposed=true}}}}});
 const event={request:new Request('https://example.invalid',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"not_messages":"PRIVATE_SENTINEL"}'})};
 const original=event.request;await callback(event);assert.equal(event.request,original);
 cleanup();assert.equal(disposed,true);
});
