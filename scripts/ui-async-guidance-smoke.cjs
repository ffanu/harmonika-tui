'use strict';
// Synthetic decision probe. No browser/client function is executed here.
const fs=require('node:fs'),http=require('node:http');
const key=fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim();
const request={model:'harmonika-ai',stream:false,messages:[{role:'user',content:'Verify this synthetic ticket UI against its 36px minimum touch target requirement. The list container has loaded and buttons measure44px, but the .attachments area is still loading its <a download> attachment links, which have not appeared. Do the necessary next check before deciding whether the UI passes.'}],tools:[{type:'function',function:{name:'wait_and_measure_controls',parameters:{type:'object',properties:{wait_for:{type:'string'},selector:{type:'string'},min_height:{type:'integer'}},required:['wait_for','selector','min_height'],additionalProperties:false}}}],tool_choice:'required',parallel_tool_calls:false};
const body=JSON.stringify(request);
const r=http.request('http://127.0.0.1:17890/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json','X-Harmonika-Client':'internal-agent','X-Harmonika-Agent-Secret':key,'Content-Length':Buffer.byteLength(body)}},response=>{
 let buffer='';response.on('data',x=>buffer+=x);response.on('end',()=>{
  try{
   const value=JSON.parse(buffer),message=value.choices?.[0]?.message,call=message?.tool_calls?.[0],args=JSON.parse(call?.function?.arguments||'{}');
   const checks={http200:response.statusCode===200,declared_tool:call?.function?.name==='wait_and_measure_controls',waits_for_attachment_link:typeof args.wait_for==='string'&&/(?:\.attachments.*\ba\b|a\[download\])/.test(args.wait_for),includes_download_links:typeof args.selector==='string'&&/(?:\ba\b|link)/.test(args.selector),requested_minimum:args.min_height===36,internal_state_private:message&&!Object.hasOwn(message,'task_state')};
   const passed=Object.values(checks).every(Boolean);console.log(JSON.stringify({passed,checks,client_tools_executed:false,scope:'synthetic_decision_only'}));process.exitCode=passed?0:1;
  }catch{console.log('{"passed":false,"error_type":"invalid_probe_response"}');process.exitCode=1;}
 });
});
r.on('error',()=>{console.log('{"passed":false,"error_type":"probe_transport_error"}');process.exitCode=1;});
r.setTimeout(90000,()=>r.destroy(Error('probe_timeout')));r.end(body);
