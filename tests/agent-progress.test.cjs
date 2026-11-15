'use strict';
const {test}=require('node:test'),a=require('node:assert/strict');
const agent=require('../backend/planner/agent-controller.cjs');
function history(entries){
  return {messages:[{role:'user',content:'Diagnose the existing fixture job.'},...entries.flatMap(([name,path,content],i)=>[
    {role:'assistant',tool_calls:[{id:'r'+i,function:{name,arguments:JSON.stringify({path})}}]},
    {role:'tool',tool_call_id:'r'+i,content},
  ])]};
}
const decide=(name='opaque_a',path='a')=>({tool_calls:[{name,arguments:JSON.stringify({path})}],task_state:{phase:'verify',remaining:['diagnosis'],evidence_call_ids:[],next_step:''}});
const alternating=()=>history([['opaque_a','a',''],['opaque_b','b',''],['opaque_a','a',''],['opaque_b','b','']]);
test('alternating opaque tool names cannot bypass the empty-check bound',()=>{
  const data=alternating();
  for(const [name,path] of [['opaque_a','a'],['opaque_b','b']])a.throws(()=>agent.check(decide(name,path),data),e=>e.decisionReason==='stalled_decision');
});
test('alternating arguments of the same tool cannot bypass the bound',()=>{
  const data=history([['opaque_a','a',''],['opaque_a','b',''],['opaque_a','a',''],['opaque_a','b','']]);
  a.throws(()=>agent.check(decide(),data),e=>e.decisionReason==='stalled_decision');
});
test('a different relevant status request remains available',()=>{
  a.doesNotThrow(()=>agent.check(decide('opaque_c','status'),alternating()));
});
test('a nonempty intervening receipt permits a fresh check without inferring tool names',()=>{
  const data=alternating();data.messages.push({role:'assistant',tool_calls:[{id:'changed',function:{name:'arbitrary',arguments:'{}'}}]},{role:'tool',tool_call_id:'changed',content:'{"ok":true,"saved":true}'});
  a.doesNotThrow(()=>agent.check(decide(),data));
});
test('a second check is allowed; only the next repeated empty attempt is blocked',()=>{
  const data=history([['opaque_a','a',''],['opaque_b','b','']]);
  a.doesNotThrow(()=>agent.check(decide(),data));
});
test('nonempty verification receipts retain existing verification semantics',()=>{
  const data=history([['opaque_a','a','{"exit_code":0}'],['opaque_a','a','{"exit_code":0}']]);
  a.doesNotThrow(()=>agent.check(decide(),data));
});
test('new tasks do not inherit a previous task empty-check restriction',()=>{
  const data=alternating();data.messages.push({role:'user',content:'Audit a different fixture now.'});
  a.doesNotThrow(()=>agent.check(decide(),data));
});
test('progress mechanics expose counts only, never raw arguments or output',()=>{
  const data=alternating();data.messages[1].tool_calls[0].function.arguments='{"path":"private_fixture_reference"}';
  const meta=agent.check(decide('different','status'),data);
  a.equal(meta.max_empty_check_repeats,2);a.ok(!JSON.stringify(meta).includes('private_fixture_reference'));
});
test('unrelated nonempty reads cannot permit an unbounded empty-poll loop',()=>{
  const entries=[];for(let i=0;i<6;i++)entries.push(['opaque_a','a',''],['opaque_read','note','unchanged note']);
  a.throws(()=>agent.check(decide(),history(entries)),e=>e.decisionReason==='stalled_decision');
});
test('bounded fresh checks after intervening changes still remain available',()=>{
  const entries=[];for(let i=0;i<5;i++)entries.push(['opaque_a','a',''],['opaque_change','state','{"ok":true}']);
  a.doesNotThrow(()=>agent.check(decide(),history(entries)));
});
test('a nonempty result from that same signature resets its total empty budget',()=>{
  const entries=[];for(let i=0;i<6;i++)entries.push(['opaque_a','a',''],['opaque_read','note','note']);
  entries.push(['opaque_a','a','{"status":"completed"}']);
  a.doesNotThrow(()=>agent.check(decide(),history(entries)));
});
test('a new task resets the total empty budget as well as the short window',()=>{
  const entries=[];for(let i=0;i<6;i++)entries.push(['opaque_a','a',''],['opaque_read','note','note']);
  const data=history(entries);data.messages.push({role:'user',content:'Start a different fixture task.'});
  a.doesNotThrow(()=>agent.check(decide(),data));
});
