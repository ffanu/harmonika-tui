'use strict';
// One bounded stdio invocation. Credentials stay in the inherited environment;
// only the selected tool's arguments travel over stdin, never command arguments.
const fs=require('node:fs');
const {createCore}=require('./mcp-core.cjs');
const {createMediaBridge}=require('./media_bridge.cjs');
(async()=>{
 let raw='';for await(const part of process.stdin){raw+=part;if(Buffer.byteLength(raw)>1024*1024)throw Error('input_limit');}
 const request=JSON.parse(raw),bridge=createMediaBridge(),core=createCore({mediaBridge:bridge});
 try {
  const result=await core.callTool(request.name,request.arguments);
  const value=result.content?.find(x=>x.type==='text')?.text;
  process.stdout.write(value||JSON.stringify({ok:false,error:'empty_media_result'}));
 } finally {core.shutdown();bridge.close();}
})().catch(()=>{process.stdout.write(JSON.stringify({ok:false,error:'media_adapter_unavailable',retry_safe:false}));process.exitCode=2;});
