'use strict';
// Synthetic controller probes only: no model, executor, customer or credentials.
const path=require('node:path');
const agent=require(path.join(process.argv[2]||path.join(__dirname,'../backend/planner'),'agent-controller.cjs'));
const tap='# tests 2\n# pass 2\n# fail 0\n';
const examples=[
 ['pending_child',['fixture-submit','ls'],[{query:'fixture-submit',success:true,result:'accepted',status:'queued',job_id:'fixture_job'},{query:'ls',success:true,result:'README.md'}]],
 ['unknown_child',['fixture-submit','ls'],[{query:'fixture-submit',success:true,result:'ack lost',status:'outcome_unknown',job_id:'fixture_job'},{query:'ls',success:true,result:'README.md'}]],
 ['metadata_pending',['fixture-submit','ls'],[{query:'fixture-submit',success:true,result:'accepted',metadata:{status:'running',job_id:'fixture_job'}},{query:'ls',success:true,result:'README.md'}]],
 ['missing_child',['fixture-submit','ls'],[{query:'ls',success:true,result:'README.md'}]],
 ['duplicate_test_child',['node --test test/a.cjs','node --test test/b.cjs'],[{query:'node --test test/a.cjs',success:true,result:tap},{query:'node --test test/a.cjs',success:true,result:tap}]],
 ['foreign_child',['fixture-submit'],[{query:'unrequested-command',success:true,result:'done'}]],
];
const rows=examples.map(([name,commands,result])=>{
 const data={messages:[{role:'user',content:'Verifikasi fixture dalam scope.'},
  {role:'assistant',tool_calls:[{id:'batch',function:{name:'run_commands',arguments:JSON.stringify({commands})}}]},
  {role:'tool',tool_call_id:'batch',content:JSON.stringify(result)},
  {role:'assistant',tool_calls:[{id:'read',function:{name:'fixture.read',arguments:'{"path":"README.md"}'}}]},
  {role:'tool',tool_call_id:'read',content:'{"content":"fixture"}'}]};
 try{agent.check({content:'Pemeriksaan selesai.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:['read'],next_step:''}},data);return {name,accepted:true,reason:null};}
 catch(e){return {name,accepted:false,reason:e.decisionReason};}
});
console.log(JSON.stringify({synthetic_only:true,model_called:false,tools_executed:false,rows}));
