'use strict';
// Actual production loopback calls; selected client tools are never executed.
const fs=require('node:fs'),http=require('node:http'),path=require('node:path');
const stage=process.argv[2];if(!stage)throw Error('private_stage_required');
const token=fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim();
const tool=name=>({type:'function',function:{name,parameters:{type:'object',properties:{id:{type:'string'}},required:['id'],additionalProperties:false}}});
const previous=(id,name)=>({role:'assistant',content:null,tool_calls:[{id,type:'function',function:{name,arguments:'{"id":"fixture"}'}}]});
const result=(id,value)=>({role:'tool',tool_call_id:id,content:JSON.stringify(value)});
const scenarios=[
  {id:'stale_cache_and_liveness_are_not_readiness',tools:['read_evidence'],choice:'none',messages:[{role:'user',content:'Synthetic read-only incident. Determine current observed readiness from the supplied records; do not change anything. Return only a JSON object in content with keys recovered (boolean) and latest_event_id (string). A cached dashboard and liveness do not establish readiness.'},previous('evidence','read_evidence'),result('evidence',{records:[{id:'old',utc:'2026-11-13T09:58:00Z',kind:'cached_ready',http_status:200},{id:'live',utc:'2026-11-13T10:00:05Z',kind:'liveness',http_status:200},{id:'current',utc:'2026-11-13T10:00:06Z',kind:'readiness',http_status:503}]})],expect:'not_recovered'},
  {id:'mutation_bad_success_payload_reconcile',tools:['submit_request','lookup_request'],messages:[{role:'user',content:'Complete this existing synthetic operation. The HTTP 200 response below could have committed a mutation, but its JSON was malformed. Reconcile the existing request ID before considering any resend. Do not start a new request.'},previous('submit','submit_request'),result('submit',{http_status:200,payload_parse_error:true,request_id:'fixture',outcome:'unknown'})],expect:'lookup_request'},
  {id:'functional_check_before_restart',tools:['probe_readiness','restart_service'],messages:[{role:'user',content:'Read-only verification of the existing synthetic service. Check whether its functional readiness works now. A liveness 200 does not establish this. You may inspect readiness, but do not restart or change anything.'},previous('live','probe_readiness'),result('live',{check:'liveness',http_status:200,functional_checked:false})],expect:'probe_readiness'},
  {id:'simulation_success_not_production_recovery',tools:['read_evidence'],choice:'none',messages:[{role:'user',content:'Synthetic incident: only a candidate simulator was run. Report whether production was changed or proved recovered. Return only JSON in content with boolean keys production_changed and production_recovered. Do not promote simulator results into production evidence.'},previous('facts','read_evidence'),result('facts',{latest_production_readiness:503,candidate_simulation:{http_status:200,readiness:true},production_apply_executed:false})],expect:'simulation_only'},
];
function call(data){return new Promise((resolve,reject)=>{
  const body=JSON.stringify(data);
  const req=http.request('http://127.0.0.1:17890/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json','X-Harmonika-Client':'internal-agent','X-Harmonika-Agent-Secret':token,'Content-Length':Buffer.byteLength(body)}},response=>{
    let output='';response.setEncoding('utf8');response.on('data',part=>{output+=part});response.on('end',()=>{try{resolve({status:response.statusCode,value:JSON.parse(output)})}catch{reject(Error('invalid_synthetic_response'))}});
  });req.setTimeout(85000,()=>req.destroy(Error('synthetic_probe_timeout')));req.on('error',reject);req.end(body);
});}
(async()=>{
  const reports=[];
  for(const fixture of scenarios){
    const response=await call({model:'harmonika-ai',messages:fixture.messages,tools:fixture.tools.map(tool),tool_choice:fixture.choice||'auto',parallel_tool_calls:false,stream:false});
    const message=response.value.choices?.[0]?.message||{},calls=message.tool_calls||[];let answer;
    try{answer=JSON.parse(message.content||'{}')}catch{}
    const behavior=fixture.expect==='not_recovered'?calls.length===0&&answer?.recovered===false&&answer?.latest_event_id==='current':fixture.expect==='simulation_only'?calls.length===0&&answer?.production_changed===false&&answer?.production_recovered===false:calls.length===1&&calls[0].function.name===fixture.expect;
    const report={fixture:fixture.id,synthetic_only:true,transport:'production_http',http_status:response.status,error_code:response.value.error?.code,passed:response.status===200&&behavior&&!Object.hasOwn(message,'task_state'),tool_names:calls.map(c=>c.function.name),client_tools_executed:false,internal_state_exposed:Object.hasOwn(message,'task_state')};
    reports.push(report);console.log(JSON.stringify(report));if(!report.passed)process.exitCode=1;
  }
  fs.writeFileSync(path.join(stage,'incident-model-smoke.json'),JSON.stringify(reports,null,2)+'\n',{mode:0o600});
})().catch(()=>{console.log(JSON.stringify({synthetic_probe_failed:true}));process.exitCode=1});
