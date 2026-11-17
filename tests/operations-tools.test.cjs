'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events'),{PassThrough}=require('node:stream');
const tools=require('../runtime/tools/operations-tools.cjs'),core=require('../runtime/tools/mcp-core.cjs');
test('core exposes one operations tool and routes exact validated input without losing its payload',async()=>{
 const calls=[],client=core.createCore({operations:async(sid,input)=>{calls.push({sid,input});return{ok:true,result:{test_count:5,passed:5,status:'passed'}}}});
 assert.equal(client.listTools().filter(t=>t.name==='engine_operations').length,1);
 const data={skill_id:'operations.skills.selftest',input:{suite:'core'}};
 const result=await client.callTool('engine_operations',data);assert.equal(result.isError,false);assert.deepEqual(calls,[{sid:data.skill_id,input:data.input}]);
 for(const invalid of [{...data,actor:'api-token:1'},{...data,skill_id:'exec.shell'},{...data,input:[]},{skill_id:data.skill_id}])assert.equal((await client.callTool('engine_operations',invalid)).isError,true);
 assert.equal(calls.length,1);
});
test('operations child uses stdin for input and never accepts arbitrary executables or tool IDs',async()=>{
 let seen;
 const execute=(exe,args,opts)=>{
  seen={exe,args,opts};const child=new EventEmitter();child.stdout=new PassThrough();child.stdin=new PassThrough();child.kill=()=>{};
  child.stdin.on('finish',()=>{child.stdout.write(JSON.stringify({ok:true,result:{status:'completed'}}));child.emit('close',0)});return child;
 };
 const result=await tools.invoke('operations.engine.diagnose',{}, {python:'/fixture/python',execute});assert.equal(result.ok,true);assert.equal(seen.exe,'/fixture/python');assert.deepEqual(seen.args.slice(-2),['invoke','operations.engine.diagnose']);assert.equal(seen.args.some(v=>v.includes('hai_live_')),false);
 assert.equal((await tools.invoke('exec.shell',{}, {execute:()=>{throw Error('must_not_execute')}})).ok,false);
});
test('unknown child completion retains non-replayable state instead of success',async()=>{
 const execute=()=>{const child=new EventEmitter();child.stdout=new PassThrough();child.stdin=new PassThrough();child.kill=()=>{};child.stdin.on('finish',()=>{child.stdout.write('not-json');child.emit('close',0)});return child};
 const result=await tools.invoke('operations.deploy.apply',{}, {execute});assert.equal(result.ok,false);assert.equal(result.retry_safe,false);assert.equal(result.operation_state,'unknown');
});
