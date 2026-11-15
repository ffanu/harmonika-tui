'use strict';
// Owner-only, isolated model probes. No client command is executed.
const fs=require('node:fs'),Module=require('node:module'),path=require('node:path');
const stage=process.argv[2];
if(!stage)throw Error('private_stage_required');
const base='/opt/harmonika-ai/api/internal-tool-planner.js';
const m=new Module(base,module);m.filename=base;m.paths=Module._nodeModulePaths(path.dirname(base));
const original=m.require.bind(m);
m.require=function(name){
  if(['./decision-recovery.cjs','./secret-firewall.cjs'].includes(name))return require(path.resolve(stage,'backend/planner',name.slice(2)));
  return original(name);
};
m._compile(fs.readFileSync(path.resolve(stage,'candidate.js'),'utf8'),base);
const req={headers:{'x-harmonika-agent-secret':fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim()},socket:{remoteAddress:'127.0.0.1'}};
const tools=[{type:'function',function:{name:'run_commands',parameters:{type:'object',properties:{commands:{type:'array',items:{type:'string'}}},required:['commands'],additionalProperties:false}}}];
(async()=>{
  const fixtures=[
    {id:'missing_reference',choice:'none',messages:[
      {role:'user',content:'Synthetic fixture. Check the private reference and explain what its presence check proves. Do not print credentials.'},
      {role:'assistant',content:null,tool_calls:[{id:'reference-check',type:'function',function:{name:'run_commands',arguments:JSON.stringify({commands:["python3 -c 'print(False)'"]})}}]},
      {role:'tool',tool_call_id:'reference-check',content:'private_password_reference_available=False'}]},
    {id:'required_tool',choice:{type:'function',function:{name:'run_commands'}},messages:[{role:'user',content:'Synthetic fixture: select run_commands with one command printf fixture. Do not execute it yourself.'}]},
  ];
  for(const test of fixtures){
    let status;
    const res={writeHead(s){status=s},end(body){
      const value=JSON.parse(body),c=value.choices?.[0],calls=c?.message?.tool_calls||[];
      const passed=status===200&&(test.id==='missing_reference'?calls.length===0&&!!c?.message?.content:calls.length===1&&calls[0].function.name==='run_commands');
      console.log(JSON.stringify({fixture:test.id,passed,http_status:status,error_code:value.error?.code,tool_count:calls.length,has_text:!!c?.message?.content,finish_reason:c?.finish_reason,client_tools_executed:false}));
      if(!passed)process.exitCode=1;
    }};
    await m.exports.handle(req,res,{messages:test.messages,tools,tool_choice:test.choice,parallel_tool_calls:false,stream:false});
  }
})().catch(()=>{console.log(JSON.stringify({fixture_failure:true}));process.exitCode=1});
