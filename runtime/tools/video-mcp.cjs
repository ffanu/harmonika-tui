#!/usr/bin/env node
'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {createClient,VideoError}=require('./video-owner-client.cjs');
const ids=/^[a-f0-9]{32}$/;
const TOOLS=[{name:'video',description:'Harmonika Ai asynchronous video workflow. action=capabilities checks readiness; action=generate submits a silent preview at the explicitly requested qualified duration and returns a request/job ID; action=status checks the same job_id or request_id; action=download saves a completed MP4. action=animate uses reference_file (an approved local PNG/JPEG/WebP, max8MiB/4096px) plus prompt to animate that image: currently1second only. Do not silently replace an image-conditioned request with text-only generation. Check capabilities for qualified durations (1 or 10 seconds). Never shorten a requested duration. Raw generation has no audio; the complete workflow supports sound. For a video with sound, generate the visual clip, poll until completed, then action=narrate with job_id, spoken text and optional language id/en: creates a real MP4 with synthesized narration. Narration must fit the video; do not truncate it. Four approved neural voice presets: wanita=Wanita 1, wanita-2=Wanita 2, pria=Pria 1, pria-2=Pria 2. Follow any explicit voice request; otherwise select for the requested context, preserve an established choice within the same narration, and never switch speakers mid-clip. Default quality is neural. For a cheerful promotional offer, use style promosi_ceria and warm sales copy, preserving factual accuracy; this is phrase delivery, not native emotion control; maximum 600 UTF-8 bytes for this preset. No voice cloning. Basic robotic speech is only an explicit quality basic fallback. Never re-submit pending/uncertain work. Download verifies SHA-256; claim a saved file only after download succeeds.',inputSchema:{type:'object',properties:{action:{type:'string',enum:['capabilities','generate','status','download','narrate','animate']},style:{type:'string',enum:['normal','promosi_ceria']},voice:{type:'string',enum:['wanita','pria','wanita-2','pria-2']},quality:{type:'string',enum:['neural','basic']},text:{type:'string',maxLength:1200},language:{type:'string',enum:['id','en']},speed:{type:'integer',minimum:120,maximum:220},reference_file:{type:'string',maxLength:4096},prompt:{type:'string',minLength:1,maxLength:8000},duration_seconds:{type:'integer',enum:[1,10]},request_id:{type:'string',pattern:'^[a-f0-9]{32}$'},job_id:{type:'string',pattern:'^[a-f0-9]{32}$'},output_file:{type:'string',maxLength:4096}},required:['action'],additionalProperties:false},annotations:{readOnlyHint:false,idempotentHint:false}}];
function approvedOutput(raw,jid,options={}){
 const home=options.home||os.homedir(),cwd=options.cwd||process.cwd();const core=options.core||require(path.join(home,'.config/harmonika-terminal/current/tools/mcp-core.cjs'));
 const extension=options.extension||'.mp4';if(!['.mp4','.wav'].includes(extension))throw new VideoError('invalid_output_format');const base=path.join(home,'Downloads/harmonika-ai');const auto=path.join(base,extension==='.wav'?'audio':'video');fs.mkdirSync(auto,{recursive:true,mode:0o700});
 if(raw!==undefined&&(typeof raw!=='string'||raw.includes('\0')||raw.length>4096))throw new VideoError('invalid_output_path');
 const dest=raw?path.resolve(cwd,raw):path.join(auto,(extension==='.wav'?'harmonika-speech-':'harmonika-video-')+jid+extension);
 if(path.extname(dest).toLowerCase()!==extension||core.isPrivatePath(dest))throw new VideoError('invalid_output_path');
 const parent=fs.realpathSync(path.dirname(dest));if(core.isPrivatePath(parent))throw new VideoError('private_output_not_allowed');
 const autoReal=fs.realpathSync(base);if(autoReal!==base||fs.realpathSync(auto)!==auto)throw new VideoError('symlink_output_not_allowed');let root=core.isUnderOrEqual(base,dest)?autoReal:core.validateProjectCwd(cwd,home,dest);
 if(!core.isUnderOrEqual(root,parent))throw new VideoError('output_outside_project');
 if(fs.existsSync(dest)&&fs.lstatSync(dest).isSymbolicLink())throw new VideoError('symlink_output_not_allowed');return dest;
}
function readReference(raw,options={}){
 const home=options.home||os.homedir(),cwd=options.cwd||process.cwd(),core=options.core||require(path.join(home,'.config/harmonika-terminal/current/tools/mcp-core.cjs'));
 if(typeof raw!=='string'||raw.includes('\0'))throw new VideoError('invalid_reference_path');const source=path.resolve(cwd,raw),real=fs.realpathSync(source);if(core.isPrivatePath(real))throw new VideoError('private_reference_not_allowed');
 const downloads=fs.realpathSync(path.join(home,'Downloads'));if(!core.isUnderOrEqual(downloads,real)){const root=core.validateProjectCwd(cwd,home,real);if(!core.isUnderOrEqual(root,real))throw new VideoError('reference_outside_project');}
 const stat=fs.statSync(real);if(!stat.isFile()||stat.size<100||stat.size>8*1024*1024)throw new VideoError('invalid_reference_image');const bytes=fs.readFileSync(real);if(!bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))&&!bytes.subarray(0,3).equals(Buffer.from([255,216,255]))&&!(bytes.subarray(0,4).toString()==='RIFF'&&bytes.subarray(8,12).toString()==='WEBP'))throw new VideoError('invalid_reference_image');return bytes;
}
function createTools(options={}){
 const client=options.client||createClient();const pendingDir=options.pendingDir||path.join(os.homedir(),'.config/harmonika/video-routing');
 const journal=path.join(pendingDir,crypto.createHash('sha256').update(options.cwd||process.cwd()).digest('hex')+'.json');
 function save(v){fs.mkdirSync(pendingDir,{recursive:true,mode:0o700});const t=journal+'.'+crypto.randomBytes(8).toString('hex');fs.writeFileSync(t,JSON.stringify({request_id:v.request_id,job_id:v.job_id,state:v.state}),{mode:0o600,flag:'wx'});fs.renameSync(t,journal);}
 function previous(){try{return JSON.parse(fs.readFileSync(journal,'utf8'));}catch{return {};}}
 async function call(name,args={}){
  try{
   if(!args||typeof args!=='object'||Array.isArray(args))throw new VideoError('invalid_arguments');
   if(name==='video'){const {action,...rest}=args;const mapped={capabilities:'video_capabilities',generate:'generate_video',status:'video_job_status',download:'download_video',narrate:'narrate_video',animate:'animate_video'}[action];if(!mapped)throw new VideoError('invalid_video_action');return await call(mapped,rest);}
   const allowed={video_capabilities:[],generate_video:['prompt','duration_seconds','request_id'],animate_video:['prompt','duration_seconds','request_id','reference_file'],video_job_status:['job_id','request_id'],download_video:['job_id','output_file'],narrate_video:['job_id','text','language','speed','voice','quality','style','output_file']}[name];
   if(!allowed||Object.keys(args).some(k=>!allowed.includes(k)))throw new VideoError('invalid_arguments');
   if(name==='video_capabilities')return await client.call(name);
   if(name==='generate_video'||name==='animate_video'){
    if(typeof args.prompt!=='string'||!args.prompt.trim()||Buffer.byteLength(args.prompt)>8000||(args.duration_seconds!==undefined&&![1,10].includes(args.duration_seconds))||(args.request_id!==undefined&&!ids.test(args.request_id)))throw new VideoError('unsupported_video_profile');
    if(name==='animate_video'&&(typeof args.reference_file!=='string'||(args.duration_seconds!==undefined&&args.duration_seconds!==1)))throw new VideoError('image_video_duration_unsupported');
    const old=previous();if(!args.request_id&&ids.test(old.request_id||'')&&!['completed','failed'].includes(old.state)){
     try{const state=await client.call('video_request_status',{request_id:old.request_id});if(!['completed','failed'].includes(state.state))return {...state,request_id:old.request_id,existing_job:true};}catch{return {ok:true,status:'pending',state:'uncertain',request_id:old.request_id,retry_submission:false,provider:'Harmonika Ai'};}
    }
    const rid=args.request_id||crypto.randomBytes(16).toString('hex');
    let payload={prompt:args.prompt,duration_seconds:args.duration_seconds||1,request_id:rid};if(name==='animate_video')payload.reference_image_base64=readReference(args.reference_file,options).toString('base64');
    save({request_id:rid,state:'submitting'});let out;try{out=await client.call(name,payload);}catch(e){if(e.code!=='video_submission_uncertain'){save({request_id:rid,state:'failed'});throw e;}try{out=await client.call('video_request_status',{request_id:rid});}catch{out={ok:true,status:'pending',state:'uncertain'};}}
    out.request_id=rid;save(out);return out;
   }
   if(name==='video_job_status'){
    if((!ids.test(args.job_id||''))===(!ids.test(args.request_id||'')))throw new VideoError('exactly_one_job_or_request_required');
    const out=await client.call(args.job_id?'video_job_status':'video_request_status',args);const old=previous();if(args.job_id===old.job_id||args.request_id===old.request_id)save({...old,...out});return out;
   }
   if(!ids.test(args.job_id||''))throw new VideoError('invalid_job_id');const narrated=name==='narrate_video';const payload={job_id:args.job_id};if(narrated)for(const k of ['text','language','speed','voice','quality','style'])if(args[k]!==undefined)payload[k]=args[k];
   const out=await client.call(narrated?'narrate_video':'download_video',payload);const dest=approvedOutput(args.output_file,narrated?out.sha256?.slice(0,32):args.job_id,options);if(out.mime_type!=='video/mp4'||typeof out.video_base64!=='string'||out.video_base64.length>12*1024*1024||!/^[a-f0-9]{64}$/.test(out.sha256||''))throw new VideoError('invalid_video_artifact');
   const raw=Buffer.from(out.video_base64,'base64');if(raw.length!==out.bytes||raw.length>8*1024*1024||raw.length<12||raw.subarray(4,8).toString()!=='ftyp'||crypto.createHash('sha256').update(raw).digest('hex')!==out.sha256)throw new VideoError('video_checksum_mismatch');
   let saved=false;if(fs.existsSync(dest)){if(!fs.statSync(dest).isFile()||fs.statSync(dest).size!==raw.length||crypto.createHash('sha256').update(fs.readFileSync(dest)).digest('hex')!==out.sha256)throw new VideoError('output_exists');}else{let fd;try{fd=fs.openSync(dest,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|(fs.constants.O_NOFOLLOW||0),0o600);fs.writeFileSync(fd,raw);saved=true;}catch(e){if(fd!==undefined){fs.closeSync(fd);fd=undefined;fs.unlinkSync(dest);}throw e;}finally{if(fd!==undefined)fs.closeSync(fd);}}
   return {ok:true,status:'ok',provider:'Harmonika Ai',output_file:dest,bytes:raw.length,sha256:out.sha256,duration_seconds:out.duration_seconds,audio:!!out.audio,voice_quality:out.voice_quality,voice:out.voice,style:out.style||'normal',native_emotion_control:false,synthetic_audio:!!out.audio,new_file:saved};
  }catch(e){return {ok:false,status:'degraded',provider:'Harmonika Ai',error:{code:e instanceof VideoError?e.code:'video_unavailable'}};}
 }
 return {call,listTools:()=>TOOLS,close:()=>client.close?.()};
}
async function main(){
 const sdk=path.join(os.homedir(),'.config/harmonika/harmonika-tools/node_modules/@modelcontextprotocol/sdk/dist/cjs');
 const {Server}=require(path.join(sdk,'server/index.js'));const {StdioServerTransport}=require(path.join(sdk,'server/stdio.js'));const {ListToolsRequestSchema,CallToolRequestSchema}=require(path.join(sdk,'types.js'));
 const tools=createTools();const server=new Server({name:'harmonika-video',version:'1.0.0'},{capabilities:{tools:{}}});
 server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:tools.listTools()}));server.setRequestHandler(CallToolRequestSchema,async r=>{const out=await tools.call(r.params.name,r.params.arguments);return {content:[{type:'text',text:JSON.stringify(out)}],isError:out.ok!==true};});process.on('exit',()=>tools.close());await server.connect(new StdioServerTransport());
}
if(require.main===module)main().catch(()=>{process.stderr.write('Harmonika video MCP unavailable.\n');process.exitCode=1;});
module.exports={TOOLS,approvedOutput,createTools};
