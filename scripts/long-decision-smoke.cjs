'use strict';
// Deterministic, synthetic history; never reads or replays a user's conversation.
const fs=require('node:fs'),Module=require('node:module'),path=require('node:path'),http=require('node:http');
const stage=process.argv[2],production=process.argv.includes('--production'),stream=process.argv.includes('--stream');
const expected={a:'ALPHA-482',b:'BRAVO-729',c:'CHARLIE-135',d:'DELTA-864',e:'ECHO-297'};
const block='Synthetic stable record: no action needed.\n'.repeat(6200);
const evidence=Object.entries(expected).map(([key,value])=>'fixture_'+key+'='+value+'\n'+block).join('');
const data={model:'harmonika-ai',parallel_tool_calls:false,stream,tool_choice:'none',
  tools:[{type:'function',function:{name:'run_commands',parameters:{type:'object',properties:{commands:{type:'array',items:{type:'string'}}},required:['commands'],additionalProperties:false}}}],
  messages:[{role:'user',content:'Synthetic exam: a read command will return reference records. Use them to answer the final question.'},
    {role:'assistant',content:null,tool_calls:[{id:'synthetic-completed-read',type:'function',function:{name:'run_commands',arguments:JSON.stringify({commands:['printf synthetic-fixture']})}}]},
    {role:'tool',tool_call_id:'synthetic-completed-read',content:evidence},
    {role:'user',content:'Find fixture_a, fixture_b, fixture_c, fixture_d and fixture_e across the completed tool result. In content return a JSON object with keys a,b,c,d,e and their exact values. No commands or tools. Do not repeat completed actions.'}]};
const began=Date.now(),req={headers:{'x-harmonika-agent-secret':fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim()},socket:{remoteAddress:'127.0.0.1'}};
function result(status,body){
  let answer={},value={},done=!stream;try{
    if(stream&&status===200){
      const frames=body.split('\n').filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
      const content=frames.map(frame=>frame.choices?.[0]?.delta?.content||'').join('');
      const tool_calls=frames.flatMap(frame=>frame.choices?.[0]?.delta?.tool_calls||[]);
      done=body.includes('data: [DONE]');value={choices:[{message:{content,tool_calls}}]};
    }else value=JSON.parse(body);
    answer=JSON.parse(value.choices?.[0]?.message?.content||'{}');
  }catch{}
  const matches=Object.fromEntries(Object.entries(expected).map(([key,v])=>[key,answer[key]===v]));
  const report={synthetic_only:true,transport:production?'production_http':'staged_planner',http_status:status,error_code:value.error?.code,
    payload_chars:JSON.stringify(data).length,tool_count:value.choices?.[0]?.message?.tool_calls?.length||0,needle_matches:matches,
    passed:status===200&&done&&Object.values(matches).every(Boolean),stream,sse_done:stream?done:undefined,duration_ms:Date.now()-began,client_commands_executed:false,user_history_replayed:false};
  console.log(JSON.stringify(report));if(!report.passed)process.exitCode=1;
}
if(production){
  const body=JSON.stringify(data);
  const call=http.request('http://127.0.0.1:17890/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json','X-Harmonika-Client':'internal-agent','X-Harmonika-Agent-Secret':req.headers['x-harmonika-agent-secret'],'Content-Length':Buffer.byteLength(body)}},response=>{
    let output='';response.setEncoding('utf8');response.on('data',part=>{output+=part;});response.on('end',()=>result(response.statusCode,output));
  });
  call.setTimeout(85000,()=>{call.destroy();});call.on('error',()=>{console.log(JSON.stringify({passed:false,code:'synthetic_probe_transport_error'}));process.exitCode=1;});call.end(body);
}else{
  if(!stage)throw Error('private_stage_required');
  const base='/opt/harmonika-ai/api/internal-tool-planner.js',m=new Module(base,module);m.filename=base;m.paths=Module._nodeModulePaths(path.dirname(base));
  const original=m.require.bind(m);m.require=function(name){if(['./long-decision.cjs','./decision-recovery.cjs','./secret-firewall.cjs'].includes(name))return require(path.resolve(stage,'backend/planner',name.slice(2)));return original(name);};
  m._compile(fs.readFileSync(path.resolve(stage,'candidate.js'),'utf8'),base);
  let status;const response={writeHead(s){status=s},end(body){result(status,body);}};
  m.exports.handle(req,response,data).catch(()=>{console.log(JSON.stringify({passed:false,code:'synthetic_probe_failed'}));process.exitCode=1;});
}
