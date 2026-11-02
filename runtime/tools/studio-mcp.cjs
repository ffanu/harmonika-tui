#!/usr/bin/env node
'use strict';
const fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {createClient,canonical}=require('./video-owner-client.cjs');
const ACTIONS=['capabilities','assets','upload','autocut','subtitles','direct','revise','render','shorts','track','export_capcut','job','download'];
const TOOLS=[{name:'studio',description:'Harmonika Studio video editing. Use for editing existing videos: upload local MP4 -> poll job -> read result asset_id -> direct/autocut/subtitles/revise/render -> poll same job -> download actual result. Asynchronous jobs; queued is not success. action=assets lists owner videos. action=direct takes asset_id and params.instruction and returns an editable timeline; action=revise takes params.timeline and params.instruction. action=render takes params.asset_id and params.keep [{in,out}], optional grade/reframe/overlay/audio/transition/zoom/speed/burn_subtitles. Other actions: shorts (asset_id,params.instruction), track (asset_id,params.t0,t1,auto_face), export_capcut (asset_id,params.keep,width,height). For new video generation use the existing video tool (RTX), then upload the saved MP4 here for editing. Check capabilities first. No automatic GPU-heavy actions unless requested. API availability does not prove every model ready. Upload/download is chunked, maximum500MiB MP4. Use returned job_id to poll. Never resubmit uncertain work with a new request_id. To recover status use the recorded request_id. Unknown submissions without a remote job require operator reconciliation; never blindly re-upload or render again. Do not claim a file exists until download succeeds. Downloads saved in Downloads/harmonika-ai/studio; no vendor token required in terminal.',inputSchema:{type:'object',properties:{action:{type:'string',enum:ACTIONS},file_path:{type:'string'},output_file:{type:'string'},asset_id:{type:'string'},job_id:{type:'string'},request_id:{type:'string'},params:{type:'object',additionalProperties:true}},required:['action'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:false}}];
function createTools(options={}){
 const client=options.client||createClient(),home=options.home||os.homedir(),cwd=options.cwd||process.cwd();
 const core=options.core||require(path.join(home,'.config/harmonika-terminal/current/tools/mcp-core.cjs'));
 const state=options.state||path.join(home,'.config/harmonika/studio-state');fs.mkdirSync(state,{recursive:true,mode:0o700});
 function stateKey(key){if(typeof key!=='string'||! /^[A-Za-z0-9_-]{1,120}$/.test(key))throw Error('studio_invalid_request_id');return key;}
 function save(key,v){stateKey(key);const p=path.join(state,key+'.json'),t=p+'.'+crypto.randomBytes(6).toString('hex');fs.writeFileSync(t,JSON.stringify(v),{mode:0o600,flag:'wx'});fs.renameSync(t,p);}
 function load(key){stateKey(key);try{return JSON.parse(fs.readFileSync(path.join(state,key+'.json'),'utf8'))}catch{return null}}
 function allowed(raw,write=false){
  if(typeof raw!=='string'||raw.includes('\0'))throw Error('studio_invalid_path');const abs=path.resolve(cwd,raw);
  if(core.isPrivatePath(abs))throw Error('studio_private_path');
  const real=write?path.join(fs.realpathSync(path.dirname(abs)),path.basename(abs)):fs.realpathSync(abs);
  if(core.isPrivatePath(real))throw Error('studio_private_path');
  const downloads=fs.realpathSync(path.join(home,'Downloads'));
  if(!core.isUnderOrEqual(downloads,real)){const root=core.validateProjectCwd(cwd,home,real);if(!core.isUnderOrEqual(root,real))throw Error('studio_path_outside_project');}
  return real;
 }
 const rpc=data=>client.call('studio',data);
 async function call(name,args={}){
  let request_id,pendingKey;
  try{
   if(name!=='studio'||!args||!ACTIONS.includes(args.action)||Object.keys(args).some(k=>!['action','file_path','output_file','asset_id','job_id','request_id','params'].includes(k)))throw Error('studio_invalid_arguments');
   if(args.request_id!==undefined)stateKey(args.request_id);
   const action=args.action;
   if(action==='upload'){
    const file=allowed(args.file_path),stat=fs.statSync(file);
    if(!stat.isFile()||stat.size<12||stat.size>500*1024*1024||path.extname(file).toLowerCase()!=='.mp4')throw Error('studio_invalid_upload');
    const fd=await fsp.open(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
    try{
     const prefix=Buffer.alloc(12);await fd.read(prefix,0,12,0);if(prefix.subarray(4,8).toString()!=='ftyp')throw Error('studio_invalid_mp4');
     const hash=crypto.createHash('sha256');let at=0,buffer=Buffer.alloc(512*1024);
     while(at<stat.size){const {bytesRead}=await fd.read(buffer,0,Math.min(buffer.length,stat.size-at),at);if(!bytesRead)throw Error('studio_file_changed');hash.update(buffer.subarray(0,bytesRead));at+=bytesRead;}
     const sha=hash.digest('hex'),key=crypto.createHash('sha256').update(file+sha).digest('hex'),old=load(key);
     request_id=args.request_id||old?.request_id||crypto.randomBytes(16).toString('hex');save(key,{request_id,sha256:sha});
     let u=await rpc({action:'upload_begin',filename:path.basename(file),bytes:stat.size,sha256:sha,request_id});
     if(!Number.isInteger(u.offset)||u.offset<0||u.offset>stat.size||!/^up_[a-f0-9]{32}$/.test(u.upload_id))throw Error('studio_bad_upload_response');
     let offset=u.offset;
     while(offset<stat.size){const {bytesRead}=await fd.read(buffer,0,Math.min(buffer.length,stat.size-offset),offset);if(!bytesRead)throw Error('studio_file_changed');const chunk=await rpc({action:'upload_chunk',upload_id:u.upload_id,offset,data:buffer.subarray(0,bytesRead).toString('base64')});if(chunk.offset!==offset+bytesRead)throw Error('studio_bad_chunk_response');offset=chunk.offset;}
     const result=await rpc({action:'upload_finish',upload_id:u.upload_id});save(key,{request_id,sha256:sha,job_id:result.job_id});return result;
    }finally{await fd.close();}
   }
   if(action==='download'){
    const job=await rpc({action:'job',job_id:args.job_id});const a=job.result?.artifact;
    if(job.state!=='completed'||!a||!Number.isInteger(a.bytes)||a.bytes<12||a.bytes>500*1024*1024||!/^[a-f0-9]{64}$/.test(a.sha256))throw Error('studio_artifact_not_ready');
    const extension=a.mime_type==='video/mp4'?'.mp4':a.mime_type?.includes('zip')?'.zip':null;if(!extension)throw Error('studio_invalid_artifact');
    const directory=path.join(home,'Downloads/harmonika-ai/studio');fs.mkdirSync(directory,{recursive:true,mode:0o700});
    const dest=allowed(args.output_file||path.join(directory,args.job_id+extension),true);if(path.extname(dest)!==extension||fs.existsSync(dest))throw Error('studio_output_exists_or_format');
    const temp=dest+'.'+crypto.randomBytes(6).toString('hex')+'.part',fd=await fsp.open(temp,'wx',0o600);const hash=crypto.createHash('sha256');let offset=0;
    try{
     while(offset<a.bytes){const c=await rpc({action:'download',job_id:args.job_id,offset});const b=Buffer.from(c.data||'','base64');if(c.offset!==offset||c.sha256!==a.sha256||!b.length||b.length>512*1024||offset+b.length>a.bytes)throw Error('studio_invalid_download');if(offset===0&&((extension==='.mp4'&&b.subarray(4,8).toString()!=='ftyp')||(extension==='.zip'&&b.subarray(0,2).toString()!=='PK')))throw Error('studio_invalid_artifact');await fd.writeFile(b);hash.update(b);offset+=b.length;}
     if(hash.digest('hex')!==a.sha256)throw Error('studio_checksum_mismatch');await fd.sync();await fd.close();await fsp.link(temp,dest);await fsp.unlink(temp);
     return {ok:true,provider:'Harmonika Ai',output_file:dest,bytes:a.bytes,sha256:a.sha256,mime_type:a.mime_type};
    }catch(e){await fd.close().catch(()=>{});await fsp.unlink(temp).catch(()=>{});throw e;}
   }
   const body={action};for(const k of ['asset_id','job_id','params','request_id'])if(args[k]!==undefined)body[k]=args[k];
   if(!['capabilities','assets','job'].includes(action)){
    pendingKey='pending-'+crypto.createHash('sha256').update(canonical([action,args.asset_id??null,args.params??{}])).digest('hex');
    const old=load(pendingKey);
    if(!args.request_id&&old?.request_id&&!['completed','failed'].includes(old.state)){
     request_id=old.request_id;
     try{const previous=await rpc({action:'job',request_id});if(!['completed','failed'].includes(previous.state))return {...previous,request_id,existing_job:true};request_id=undefined;}
     catch(e){if(e.code!=='studio_job_not_found')throw e;}
    }
    request_id=args.request_id||request_id||crypto.randomBytes(16).toString('hex');body.request_id=request_id;
    save(pendingKey,{request_id,action,state:'submitting',created_at:Date.now()});save(request_id,{request_id,action,state:'submitting',created_at:Date.now()});
   }
   const result=await rpc(body);if(request_id){const v={request_id,action,job_id:result.job_id,state:result.state};save(request_id,v);save(pendingKey,v);result.request_id=request_id;}return result;
  }catch(e){const code=e.code||e.message;
   const notDispatched=['studio_invalid_params','studio_invalid_timeline','studio_invalid_request','studio_queue_full','studio_owner_required','studio_file_too_large','studio_payload_limit','invalid_identifier','studio_use_existing_video_engine'];
   if(pendingKey&&request_id&&notDispatched.includes(code)){save(pendingKey,{request_id,state:'failed',error_code:code,created_at:Date.now()});save(request_id,{request_id,state:'failed',error_code:code,created_at:Date.now()});}
   return {ok:false,status:'degraded',provider:'Harmonika Ai',error:{code:code==='invalid_identifier'?'studio_invalid_identifier':/^(studio|video)_[a-z_]+$/.test(code)?code:'studio_unavailable'},...(request_id?{request_id,retry_submission:false}:{} )};}
 }
 return {call,listTools:()=>TOOLS,close:()=>client.close?.()};
}
async function main(){const sdk=path.join(os.homedir(),'.config/harmonika/harmonika-tools/node_modules/@modelcontextprotocol/sdk/dist/cjs');const {Server}=require(path.join(sdk,'server/index.js'));const {StdioServerTransport}=require(path.join(sdk,'server/stdio.js'));const {ListToolsRequestSchema,CallToolRequestSchema}=require(path.join(sdk,'types.js'));const tools=createTools(),server=new Server({name:'harmonika-studio',version:'1.0.0'},{capabilities:{tools:{}}});server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:tools.listTools()}));server.setRequestHandler(CallToolRequestSchema,async r=>{const v=await tools.call(r.params.name,r.params.arguments);return {content:[{type:'text',text:JSON.stringify(v)}],isError:!v.ok}});await server.connect(new StdioServerTransport());}
if(require.main===module)main().catch(()=>{process.stderr.write('Harmonika Studio MCP unavailable\n');process.exitCode=1});
module.exports={createTools,TOOLS};
