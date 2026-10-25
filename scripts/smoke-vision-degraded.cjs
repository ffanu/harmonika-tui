'use strict';
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const current=path.join(os.homedir(),'.config/harmonika-terminal/current');
const {createCore}=require(path.join(current,'tools/mcp-core.cjs'));
const {createMediaBridge}=require(path.join(current,'tools/media_bridge.cjs'));
(async()=>{
 const bridge=createMediaBridge();const caps=await bridge.capabilities();assert.equal(caps.features.image_analysis,false,'test requires unavailable vision capability');
 const project=fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-vision-degraded-'));let mentorCalls=0;
 try{fs.writeFileSync(path.join(project,'package.json'),'{"name":"vision-degraded-fixture"}');fs.writeFileSync(path.join(project,'image.png'),Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/jXQAAAAASUVORK5CYII=','base64'));
 const core=createCore({callerCwd:project,mediaBridge:bridge,rpc:async()=>{mentorCalls++;throw new Error('external fallback forbidden');}});
 const out=await core.callTool('analyze_image',{file_path:'image.png',prompt:'Read this image'});const result=JSON.parse(out.content[0].text);assert.equal(out.isError,true);assert.equal(result.status,'degraded');assert.equal(result.analysis_performed,false);assert.equal(result.processing_mode,'local_only');assert.equal(mentorCalls,0);core.shutdown();
 const report={status:'passed',installed_release:fs.realpathSync(current).split('/').pop(),live_https_capability:false,analysis_performed:false,result_status:result.status,external_mentor_calls:mentorCalls,raw_image_uploaded:false};fs.writeFileSync('/tmp/harmonika-vision-live-degraded.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{bridge.close();fs.rmSync(project,{recursive:true,force:true});}
})().catch(e=>{console.error(JSON.stringify({status:'failed',code:e.code||'smoke_failed'}));process.exitCode=1});
