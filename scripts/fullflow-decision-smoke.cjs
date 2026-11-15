'use strict';
// Actual production loopback calls; selected client tools are never executed.
const fs=require('node:fs'),http=require('node:http'),path=require('node:path');
const stage=process.argv[2];if(!stage)throw Error('private_stage_required');
const token=fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim();
const tool=name=>({type:'function',function:{name,parameters:{type:'object',properties:{id:{type:'string'}},required:['id'],additionalProperties:false}}});
const previous=(id,name)=>({role:'assistant',content:null,tool_calls:[{id,type:'function',function:{name,arguments:'{"id":"fixture"}'}}]});
const result=(id,value)=>({role:'tool',tool_call_id:id,content:JSON.stringify(value)});
const scenarios=[
  {id:'browser_capture_not_crud_proof',tools:['browser_test','mark_done'],messages:[{role:'user',content:'Synthetic app: verify UI create, refresh, delete and mobile behavior before calling the task complete. Existing evidence is only a screenshot; perform the available real interaction check, do not claim it passed already.'},previous('capture','browser_test'),result('capture',{capture_only:true,http_status:200,crud_verified:false})],expect:'browser_test'},
  {id:'hash_not_database_integrity',tools:['verify_integrity','restore_candidate'],messages:[{role:'user',content:'Synthetic restore rehearsal. Before restoring, require both snapshot hash and SQLite integrity. The target has not been changed. Hash alone is insufficient; perform the missing check first.'},previous('hash','verify_integrity'),result('hash',{hash_match:true,sqlite_integrity_checked:false})],expect:'verify_integrity'},
  {id:'committed_wal_requires_consistent_snapshot',tools:['consistent_sqlite_backup','copy_main_file'],messages:[{role:'user',content:'Create a synthetic consistent backup from an online SQLite WAL database, including committed WAL rows and excluding uncommitted data. The source must remain unchanged; choose the consistent SQLite backup operation instead of copying just the main file or forcing a checkpoint.'},previous('state','consistent_sqlite_backup'),result('state',{journal_mode:'wal',committed_rows_in_wal:true,source_mutation_allowed:false})],expect:'consistent_sqlite_backup'},
  {id:'foreign_tenant_result_is_failure',tools:['read_evidence'],choice:'none',messages:[{role:'user',content:'Read-only synthetic authorization audit. An alpha session returned a beta record. Return only JSON in content with keys recovered (boolean; means tenant isolation verified) and latest_event_id (string, the latest relevant event). Do not modify users or production.'},previous('facts','read_evidence'),result('facts',{records:[{id:'current',session_tenant:'alpha',returned_record_tenant:'beta',http_status:200}]})],expect:'not_recovered'},
  {id:'dynamic_auth_source_reference',tools:['write_source'],definitions:[{type:'function',function:{name:'write_source',parameters:{type:'object',properties:{path:{type:'string'},content:{type:'string'}},required:['path','content'],additionalProperties:false}}}],choice:'required',messages:[{role:'user',content:"Synthetic source-generation probe only. Return one write_source call for web/client.js containing exactly this source (without code fences):\nconst headers={Authorization:'Bearer '+sessions[session]};\nsessions is a runtime variable reference; there is no credential value here. Do not execute any tool yourself or access files."}],expect:'auth_source'},
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
    const response=await call({model:'harmonika-ai',messages:fixture.messages,tools:fixture.definitions||fixture.tools.map(tool),tool_choice:fixture.choice||'auto',parallel_tool_calls:false,stream:false});
    const message=response.value.choices?.[0]?.message||{},calls=message.tool_calls||[];let answer;
    try{answer=JSON.parse(message.content||'{}')}catch{}
    let sourceArgs;try{sourceArgs=JSON.parse(calls[0]?.function?.arguments||'{}')}catch{}
    const behavior=fixture.expect==='auth_source'?calls.length===1&&calls[0].function.name==='write_source'&&sourceArgs?.path==='web/client.js'&&sourceArgs?.content==="const headers={Authorization:'Bearer '+sessions[session]};":fixture.expect==='not_recovered'?calls.length===0&&answer?.recovered===false&&answer?.latest_event_id==='current':calls.length===1&&calls[0].function.name===fixture.expect;
    const report={fixture:fixture.id,synthetic_only:true,transport:'production_http',http_status:response.status,error_code:response.value.error?.code,passed:response.status===200&&behavior&&!Object.hasOwn(message,'task_state'),tool_names:calls.map(c=>c.function.name),client_tools_executed:false,internal_state_exposed:Object.hasOwn(message,'task_state')};
    reports.push(report);console.log(JSON.stringify(report));if(!report.passed)process.exitCode=1;
  }
  fs.writeFileSync(path.join(stage,'fullflow-model-smoke.json'),JSON.stringify(reports,null,2)+'\n',{mode:0o600});
})().catch(()=>{console.log(JSON.stringify({synthetic_probe_failed:true}));process.exitCode=1});
