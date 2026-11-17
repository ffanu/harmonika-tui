'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const controller=require('../backend/planner/agent-controller.cjs');
const recovery=require('../backend/planner/decision-recovery.cjs');
const completion={content:'Selesai.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:['missing'],next_step:''}};
test('missing receipt preserves rejection and exposes only a fixed mechanical subtype',()=>{
 assert.throws(()=>controller.check(completion,{messages:[{role:'user',content:'Buat file sintetis.'}]}),error=>
  error.message==='invalid_tool_response'&&error.decisionReason==='unverified_completion'&&error.decisionDetail==='unobserved_evidence');
});
test('bounded repair logs approved subtype without logging history or custom error values',async()=>{
 for(const detail of ['unsupported_success_claim','private-data-not-for-logs']){
  const events=[];let attempts=0;
  await assert.rejects(recovery.chooseDecision(async()=>{
   attempts++;
   const error=Object.assign(Error('invalid_tool_response'),{decisionReason:'unverified_completion',decisionDetail:detail,content:'PRIVATE_FIXTURE'});
   throw recovery.transportError(error);
  },{messages:[{role:'user',content:'PRIVATE_FIXTURE'}]},event=>events.push(event)),/invalid_tool_response/);
  assert.equal(attempts,2);
  const failures=events.filter(event=>event.event==='internal_tool_format_error');
  assert.equal(failures.length,2);
  assert.equal(failures[0].detail,detail==='unsupported_success_claim'?detail:undefined);
  assert.equal(JSON.stringify(events).includes('PRIVATE_FIXTURE'),false);
  assert.equal(JSON.stringify(events).includes('private-data-not-for-logs'),false);
 }
});
