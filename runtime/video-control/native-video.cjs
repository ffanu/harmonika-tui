#!/usr/bin/env node
'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const EXPECTED='db31bf846ddd1e313383dc2780874ddce44736e30af634173817bd34806e45a8';
const DESCRIPTION='Harmonika Ai video generation and image animation. For a rendering prompt, translate the user intent into concise English with concrete visible object details; preserve names, quoted text, requested duration and reference image. Answer the user in their own language. For exact product appearance or landmarks prefer the supplied reference rather than inventing it. First action=capabilities reads actual qualified model, resolution, frame rate and availability. New requests use the backend high-quality profile when activated (native 1280x720); never infer quality from an old preview limitation. action=generate uses prompt and requested duration_seconds; action=animate preserves reference_file and requested duration. Submit ONCE, retain request_id/job_id, then action=status on the same handle respecting poll_after_seconds. Without a wait tool, stop after three checks and report pending. Never submit again after an uncertain response. action=download saves a completed SHA-256-verified MP4 to Downloads; return its real path and actual model/resolution/duration/audio metadata. Queued or running is not completed. Longer requests may contain sequential reference-conditioned segments; seamless continuity is not guaranteed. Backend errors or missing qualified models must remain explicit: never substitute a smaller preview or shorten the request. Raw generated video is silent. High-quality jobs with ID prefix d04a use Studio after download/upload for narration; existing legacy jobs support action=narrate. Four existing voice presets remain available through compatible audio/Studio tools. Never claim audio, exact landmarks, or a saved file without tool evidence.';

function createAdapter(legacy,options={}){
 const tools=legacy.createTools(options);
 return {
  listTools:()=>tools.listTools().map(t=>t.name==='video'?{...t,description:DESCRIPTION}:t),
  async call(name,args){
   const out=await tools.call(name,args);
   if(out.ok && name==='video' && args?.action==='download'){
    const state=await tools.call('video',{action:'status',job_id:args.job_id});
    if(state.ok && state.state==='completed' && state.result){
     for(const key of ['width','height','fps','frames','model','models','quality','upscaled','audio'])
      if(state.result[key]!==undefined)out[key]=state.result[key];
    }
   }
   return out;
  },
  close:()=>tools.close()
 };
}

async function main(){
 const adapter=path.join(os.homedir(),'.config/harmonika-terminal/current/tools/video-mcp.cjs');
 if(crypto.createHash('sha256').update(fs.readFileSync(adapter)).digest('hex')!==EXPECTED)
  throw Error('verified_base_adapter_required');
 const sdk=path.join(os.homedir(),'.config/harmonika/harmonika-tools/node_modules/@modelcontextprotocol/sdk/dist/cjs');
 const {Server}=require(path.join(sdk,'server/index.js'));
 const {StdioServerTransport}=require(path.join(sdk,'server/stdio.js'));
 const {ListToolsRequestSchema,CallToolRequestSchema}=require(path.join(sdk,'types.js'));
 const tools=createAdapter(require(adapter));
 const server=new Server({name:'harmonika-video',version:'2026.11.13.2'},{capabilities:{tools:{}}});
 server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:tools.listTools()}));
 server.setRequestHandler(CallToolRequestSchema,async request=>{
  const out=await tools.call(request.params.name,request.params.arguments);
  return {content:[{type:'text',text:JSON.stringify(out)}],isError:out.ok!==true};
 });
 process.on('exit',()=>tools.close());await server.connect(new StdioServerTransport());
}
if(require.main===module)main().catch(()=>{process.stderr.write('Harmonika video adapter unavailable.\n');process.exitCode=1;});
module.exports={createAdapter,DESCRIPTION,EXPECTED};
