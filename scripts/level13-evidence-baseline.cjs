'use strict';
// Pure controller probes: no model, executor, network, deployment or user session.
const fs=require('node:fs');
const {createHash}=require('node:crypto');
const source=require.resolve('../backend/planner/agent-controller.cjs');
const controller=require(source);
const task={messages:[{role:'user',content:'Synthetic task: repair the lab app, run tests, and deploy the verified revision.'}]};
const unsupported={content:'Synthetic deliberately false claim: tests passed and deployment is healthy.',tool_calls:[],task_state:{phase:'answer',remaining:[],evidence_call_ids:[],next_step:''}};
const readOnly={messages:[...task.messages,
 {role:'assistant',content:null,tool_calls:[{id:'fixture_read',type:'function',function:{name:'read_file',arguments:JSON.stringify({path:'README.md'})}}]},
 {role:'tool',tool_call_id:'fixture_read',content:JSON.stringify({ok:true,content:'Synthetic README only; no tests or deploy executed.'})}
]};
const completed={...unsupported,task_state:{phase:'complete',remaining:[],evidence_call_ids:['fixture_read'],next_step:''}};
const probes=[['answer_without_execution',unsupported,task],['read_receipt_used_for_test_deploy_claim',completed,readOnly]].map(([name,value,data])=>{
 let accepted=false,reason=null;
 try{controller.check(value,data);accepted=true;}catch(error){reason=error.decisionReason||error.message;}
 return {name,validator_accepted:accepted,rejection_reason:reason};
});
console.log(JSON.stringify({status:'baseline_only',at:new Date().toISOString(),source_sha256:createHash('sha256').update(fs.readFileSync(source)).digest('hex'),synthetic:true,client_tools_executed:false,live_model_tested:false,production_changed:false,unsupported_synthetic_decisions_rejected:probes.every(p=>!p.validator_accepted),probes,interpretation:'Tests only the controller check entry point; other validation layers and actual model behavior are not established by these probes.'},null,2));
