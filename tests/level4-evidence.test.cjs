'use strict';
const test=require('node:test'),a=require('node:assert/strict');
const c=require('../backend/planner/agent-controller.cjs');
const input=()=>({messages:[{role:'user',content:'Finish this isolated deployment checkpoint.'}]});
function receipt(d,id,body){d.messages.push({role:'assistant',tool_calls:[{id,function:{name:'opaque',arguments:'{}'}}]},{role:'tool',tool_call_id:id,content:typeof body==='string'?body:JSON.stringify(body)});}
test('guide distinguishes old success, failure and fresh successful checkpoint evidence',()=>{
 const d=input();receipt(d,'old-test',{exit_code:0});receipt(d,'ack-lost',{exit_code:75});receipt(d,'checkpoint',{ok:true});
 a.deepEqual(c.evidenceMap(d),{eligible_call_ids:['old-test','checkpoint'],fresh_call_ids:['checkpoint'],ineligible_call_ids:['ack-lost']});
 a.match(c.instructions(d),/Do not rerun deployment\/submission/);
});
test('pending, unknown and empty receipts are excluded without weakening the guard',()=>{
 const d=input();receipt(d,'pending',{status:'cancel_requested'});receipt(d,'unknown',{status:'unknown'});receipt(d,'empty','');receipt(d,'proof',{ok:true});
 const map=c.evidenceMap(d);a.deepEqual(map.eligible_call_ids,['proof']);a.deepEqual(map.ineligible_call_ids,['pending','unknown','empty']);
 for(const id of ['pending','unknown'])a.throws(()=>c.check({tool_calls:[],task_state:{phase:'complete',remaining:[],next_step:'',evidence_call_ids:[id,'proof']}},d),e=>e.decisionReason==='unverified_completion');
});
test('map contains only bounded technical IDs, never receipt contents or executor arguments',()=>{
 const d=input();for(let i=0;i<40;i++)receipt(d,'r'+i,{ok:i%2===0,private_fixture_note:'do_not_copy_fixture_content'});
 const map=c.evidenceMap(d);a.ok(map.eligible_call_ids.length<=12);a.ok(map.fresh_call_ids.length<=8);a.ok(map.ineligible_call_ids.length<=8);
 a.ok(!c.instructions(d).includes('do_not_copy_fixture_content'));
 const log=c.check({tool_calls:[],task_state:{phase:'answer',remaining:[],next_step:'',evidence_call_ids:[]}},d);a.ok(!Object.hasOwn(log,'eligible_call_ids'));
});
test('new task cannot borrow evidence while a real continuation retains its receipts',()=>{
 const d=input();receipt(d,'proof',{ok:true});d.messages.push({role:'user',content:'Silakan lanjutkan.'});a.deepEqual(c.evidenceMap(d).fresh_call_ids,['proof']);
 d.messages.push({role:'user',content:'Perform a different deployment.'});a.deepEqual(c.evidenceMap(d).eligible_call_ids,[]);
});
test('no fresh evidence exists after the latest unsuccessful result',()=>{
 const d=input();receipt(d,'old',{ok:true});receipt(d,'failed',{exit_code:1});a.deepEqual(c.evidenceMap(d).fresh_call_ids,[]);
 a.throws(()=>c.check({tool_calls:[],task_state:{phase:'complete',remaining:[],next_step:'',evidence_call_ids:['old']}},d),e=>e.decisionReason==='unverified_completion');
});
test('authentication source guidance preserves runtime inputs, user edits and policy boundaries',()=>{
 const guide=c.instructions(input());
 a.match(guide,/complete parseable source module/);
 a.match(guide,/preserve unrelated code and user edits/);
 a.match(guide,/document\.getElementById\('password'\)\.value/);
 a.match(guide,/never put fixture or real passwords/);
 a.match(guide,/without repeating the same rejected request or relaxing permissions/);
});
test('browser guidance requires real async children and download-link measurements',()=>{
 const guide=c.instructions(input());
 a.match(guide,/wait for the actual asynchronous content and controls to become visible/);
 a.match(guide,/empty selector result is not passing evidence/);
 a.match(guide,/including download links, buttons, inputs, textareas and selects/);
 a.match(guide,/against the requested size at each viewport/);
});
