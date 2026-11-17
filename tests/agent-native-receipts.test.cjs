'use strict';
// Synthetic native tool histories only; no executor, files, model calls or logs.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const controller=require('../backend/planner/agent-controller.cjs');
const marker='HARMONIKA_PARITY_TOOL_OK';
const id='call_native_receipt_fixture';
const prompt='Ini uji lokal terisolasi. Hanya jalankan tool shell/bash untuk printf HARMONIKA_PARITY_TOOL_OK. Setelah hasil diterima jawab tepat marker.';
function fixture(name,content){return {messages:[
 {role:'user',content:prompt},
 {role:'assistant',content:null,tool_calls:[{id,type:'function',function:{name,arguments:JSON.stringify({command:'printf '+marker,workdir:'/tmp/harmonika-native-fixture'})}}]},
 {role:'tool',tool_call_id:id,content}],
 tools:[{type:'function',function:{name,parameters:{type:'object'}}}]};}
function completion(cited=[id]){return {content:marker,tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:cited,next_step:''}};}
const nativeBodies=[
 marker+'\n',
 [{type:'text',text:marker+'\n'}],
 JSON.stringify({content:[{type:'text',text:marker}],metadata:{exit:0}}),
 JSON.stringify({output:marker,metadata:{metadata:{exitCode:0}}}),
];

test('native shell/bash raw/text-array/MCP receipts support exact finalization without invented exit metadata',()=>{
 for(const name of ['shell','bash'])for(const body of nativeBodies){
  const data=fixture(name,body),observed=controller.observe(data);
  assert.equal(observed.results.length,1);
  assert.equal(observed.last.failed,false);assert.equal(observed.last.pending,false);
  assert.equal(observed.last.empty,false);assert.equal(observed.last.continuity.receipt_incomplete,false);
  assert.doesNotThrow(()=>controller.check(completion(),data));
 }
});

test('same native histories reject nonexistent or omitted completion evidence',()=>{
 for(const name of ['shell','bash'])for(const body of nativeBodies)for(const cited of [[],['invented_receipt']]){
  assert.throws(()=>controller.check(completion(cited),fixture(name,body)),error=>error.decisionReason==='unverified_completion');
 }
});

test('native failure and pending envelopes cannot support passing completion',()=>{
 for(const name of ['shell','bash'])for(const body of [
  JSON.stringify({content:[{type:'text',text:marker}],metadata:{exit:1}}),
  JSON.stringify({output:marker,metadata:{metadata:{exitCode:2}}}),
  JSON.stringify({output:marker,status:'running'}),
 ])assert.throws(()=>controller.check(completion(),fixture(name,body)),error=>error.decisionReason==='unverified_completion');
});

test('a new user anchor cannot borrow native receipts from the earlier task',()=>{
 const data=fixture('shell',marker);
 data.messages.push({role:'user',content:'Buat file berbeda untuk tugas baru.'});
 assert.equal(controller.observe(data).results.length,0);
 assert.throws(()=>controller.check(completion(),data),error=>error.decisionReason==='unverified_completion');
});

test('native plain marker never bypasses an operator proof contract',()=>{
 const contract={required:['tests'],checks:[]};
 assert.throws(()=>controller.check(completion(),fixture('shell',marker),()=>contract),error=>error.decisionReason==='unverified_completion');
});
