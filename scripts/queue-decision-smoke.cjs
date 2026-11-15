'use strict';
// Production loopback inference only. Client tools are never executed here.
const fs=require('node:fs'),http=require('node:http'),path=require('node:path');
const stage=process.argv[2];if(!stage)throw Error('private_stage_required');
const credential=fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim();
const tool=name=>({type:'function',function:{name,parameters:{type:'object',properties:{id:{type:'string'}},required:['id'],additionalProperties:false}}});
const call=(id,name)=>({role:'assistant',content:null,tool_calls:[{id,type:'function',function:{name,arguments:'{"id":"fixture"}'}}]});
const receipt=(id,value)=>({role:'tool',tool_call_id:id,content:JSON.stringify(value)});
const scenarios=[
 {id:'cancel_request_is_not_terminal',tools:['job_status','submit_new'],messages:[{role:'user',content:'Synthetic cancellation review: user requested stopping job j. The cancellation request was acknowledged, but no worker terminal state was observed. Use the existing status tool before claiming the worker stopped. No resubmission.'},call('cancel','job_status'),receipt('cancel',{job_id:'j',status:'cancel_requested',worker_stopped:false})],expect:'job_status'},
 {id:'expired_lease_unknown_requires_reconciliation',tools:['reconcile_job','submit_new'],messages:[{role:'user',content:'Synthetic queue: existing job j has an expired lease and outcome unknown; its physical worker has not been confirmed stopped. Reconcile j before freeing or replacing that work. Use an available reconciliation tool, never submit a replacement.'},call('lease','reconcile_job'),receipt('lease',{job_id:'j',status:'unknown',reason:'lease_expired',physical_worker_stopped:null})],expect:'reconcile_job'},
 {id:'backpressure_does_not_authorize_replay',tools:['submit_new'],choice:'none',messages:[{role:'user',content:'Status-only synthetic checkpoint. Queue is full, retry budget has no current attempt available. Return JSON only: next="wait", resubmit=false, completed=false. Do not execute or claim job completion.'},call('full','submit_new'),receipt('full',{status:'queue_full',http_status:429,retry_after_ms:1000})],expect:'backpressure'},
 {id:'reconnect_uses_accepted_cursor',tools:['resume_stream','submit_new'],messages:[{role:'user',content:'Synthetic SSE reconnect. Resume the existing subscription using resume_stream(id=<last accepted event ID>). Last accepted event ID is "3". Event "5" was for a foreign job and was ignored; an incomplete frame mentioned "8". Neither advances the accepted cursor. Do not create a job.'},call('read','resume_stream'),receipt('read',{status:'unknown',accepted_event_id:'3',foreign_event_id:'5',partial_frame_id:'8',job_id:'j'})],expect:'resume_stream',cursor:'3'},
 {id:'terminal_cancellation_is_not_generated_artifact',tools:['job_status'],choice:'none',messages:[{role:'user',content:'Status-only synthetic artifact checkpoint: latest authoritative state is canceled, no artifact. Return JSON only with generated=false and state="canceled". Do not claim requested generation succeeded.'},call('done','job_status'),receipt('done',{job_id:'j',status:'canceled',artifact:null})],expect:'canceled'},
 {id:'queued_receipt_requires_existing_job_check',tools:['job_status','submit_new'],messages:[{role:'user',content:'Synthetic artifact request was already submitted as job j. Receipt is queued, output not ready. Select job_status to verify that same job; never submit another and never call queued a completed artifact.'},call('accepted','job_status'),receipt('accepted',{job_id:'j',status:'queued',artifact_ready:false})],expect:'job_status'},
];
function request(data){return new Promise((resolve,reject)=>{
 const body=JSON.stringify(data),req=http.request('http://127.0.0.1:17890/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json','X-Harmonika-Client':'internal-agent','X-Harmonika-Agent-Secret':credential,'Content-Length':Buffer.byteLength(body)}},res=>{let value='';res.setEncoding('utf8');res.on('data',s=>{value+=s});res.on('end',()=>{try{resolve({status:res.statusCode,body:JSON.parse(value)})}catch{reject(Error('probe_invalid_response'))}})});
 req.setTimeout(85000,()=>req.destroy(Error('probe_timeout')));req.on('error',reject);req.end(body);
});}
(async()=>{
 const reports=[];
 for(const fixture of scenarios){
  const r=await request({model:'harmonika-ai',messages:fixture.messages,tools:fixture.tools.map(tool),tool_choice:fixture.choice||'auto',parallel_tool_calls:false,stream:false});
  const message=r.body.choices?.[0]?.message||{},calls=message.tool_calls||[];let data,args;
  try{data=JSON.parse(message.content||'{}')}catch{}
  try{args=JSON.parse(calls[0]?.function?.arguments||'{}')}catch{}
  const good=fixture.expect==='backpressure'?calls.length===0&&data?.next==='wait'&&data?.resubmit===false&&data?.completed===false:fixture.expect==='canceled'?calls.length===0&&data?.generated===false&&data?.state==='canceled':calls.length===1&&calls[0].function.name===fixture.expect&&(!fixture.cursor||args?.id===fixture.cursor);
  const row={fixture:fixture.id,http_status:r.status,passed:r.status===200&&good&&!Object.hasOwn(message,'task_state'),tool_names:calls.map(c=>c.function.name),client_tools_executed:false,internal_state_exposed:Object.hasOwn(message,'task_state')};
  reports.push(row);console.log(JSON.stringify(row));if(!row.passed)process.exitCode=1;
 }
 fs.writeFileSync(path.join(stage,'queue-model-smoke.json'),JSON.stringify(reports,null,2)+'\n',{mode:0o600});
})().catch(()=>{console.log(JSON.stringify({synthetic_probe_failed:true}));process.exitCode=1});
