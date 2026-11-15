'use strict';
// Synthetic decision probes against the active gateway. No client tool execution.
const fs=require('node:fs'),http=require('node:http');
const key=fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim();
function request(data){return new Promise(resolve=>{
 const body=JSON.stringify(data),r=http.request('http://127.0.0.1:17890/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json','X-Harmonika-Client':'internal-agent','X-Harmonika-Agent-Secret':key,'Content-Length':Buffer.byteLength(body)}},res=>{let b='';res.on('data',x=>{b+=x;if(b.length>262144)r.destroy();});res.on('end',()=>{try{resolve({status:res.statusCode,value:JSON.parse(b)});}catch{resolve({status:res.statusCode,value:{}});}});});r.on('error',()=>resolve({status:0,value:{}}));r.setTimeout(180000,()=>r.destroy());r.end(body);
});}
const tool=(name,properties,required)=>({type:'function',function:{name,parameters:{type:'object',properties,required,additionalProperties:false}}});
async function main(){
 const cases=[
  {id:'sqlite_platform_lock',prompt:'Fix the synthetic Python SQLite queue. A real BEGIN IMMEDIATE contention test fails on Python 3.9: the handler reads getattr(exc, "sqlite_errorcode", 0) and rethrows database is locked because that attribute is absent. Python 3.12 has the numeric attribute. Original assertion expects503 without a partial event. Choose the concrete repair policy and verification scope; never change the test or delete database.',tools:[tool('repair_sqlite_handler',{classification:{type:'string',enum:['numeric_codes_with_known_lock_fallback','all_operational_errors_retryable','numeric_attribute_only']},preserve_assertions:{type:'boolean'},verification:{type:'string',enum:['missing_code_and_real_lock_both_platforms','mac_only','none']},log:{type:'string',enum:['technical_enum_only','raw_exception']}},['classification','preserve_assertions','verification','log'])],check:a=>a.classification==='numeric_codes_with_known_lock_fallback'&&a.preserve_assertions===true&&a.verification==='missing_code_and_real_lock_both_platforms'&&a.log==='technical_enum_only'},
  {id:'committed_effect_unknown',prompt:'An isolated worker process died after an external effect committed. Queue job fixture-job is still running; current process restart is confirmed. No completion or artifact is yet verified. Choose the next action. Do not resubmit or invent another request key.',tools:[tool('lookup_effect',{job_id:{type:'string'}},['job_id']),tool('resubmit_job',{new_key:{type:'string'}},['new_key'])],name:'lookup_effect',check:a=>a.job_id==='fixture-job'},
  {id:'finite_sse_scope',prompt:'The lab API returned HTTP200 text/event-stream with Content-Length and closed after one page. Reconnect Last-Event-ID=42 returns only events with id>42. Which evidence can honestly be recorded? No continuous event delivery was observed.',tools:[tool('record_stream_evidence',{cursor_replay_verified:{type:'boolean'},continuous_live_stream_verified:{type:'boolean'}},['cursor_replay_verified','continuous_live_stream_verified'])],check:a=>a.cursor_replay_verified===true&&a.continuous_live_stream_verified===false}
 ];
 const results=[];
 for(const c of cases){
  const {status,value}=await request({model:'harmonika-ai',stream:false,messages:[{role:'user',content:c.prompt}],tools:c.tools,tool_choice:'required',parallel_tool_calls:false});
  const m=value.choices?.[0]?.message,call=m?.tool_calls?.[0];let a={};try{a=JSON.parse(call?.function?.arguments||'{}');}catch{}
  const checks={http200:status===200,one_declared_tool:m?.tool_calls?.length===1&&c.tools.some(x=>x.function.name===call?.function?.name)&&(!c.name||call.function.name===c.name),correct_decision:c.check(a),no_internal_state:m!==undefined&&!Object.hasOwn(m,'task_state')};
  results.push({case:c.id,passed:Object.values(checks).every(Boolean),checks});
 }
 const passed=results.every(x=>x.passed);console.log(JSON.stringify({passed,results,client_tools_executed:false,scope:'synthetic_decision_only'}));process.exitCode=passed?0:1;
}
main().catch(()=>{console.log('{"passed":false,"error_type":"probe_failed"}');process.exitCode=1;});
