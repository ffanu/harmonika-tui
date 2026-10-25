'use strict';
// TUI → authenticated Harmonika API → private async GPU queue.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const BASE='https://chat.harmonika.id/v1/media';
class MediaError extends Error{constructor(code,uncertain=false){super(code);this.code=code;this.uncertain=uncertain;}}
function privateKey(){let fd;try{fd=fs.openSync(path.join(os.homedir(),'.config/harmonika/harmonika-api-key'),fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0)|(fs.constants.O_NONBLOCK||0));const s=fs.fstatSync(fd);if(!s.isFile()||(process.platform!=='win32'&&((s.mode&0o077)||(typeof process.getuid==='function'&&s.uid!==process.getuid()))))throw new Error();const k=fs.readFileSync(fd,'utf8').trim();if(!/^hai_[A-Za-z0-9_]{16,240}$/.test(k))throw new Error();return k;}catch{throw new MediaError('media_key_unavailable');}finally{if(fd!==undefined)fs.closeSync(fd);}}
function createMediaBridge(options={}){
 const fetcher=options.fetch||globalThis.fetch,key=options.key||privateKey,sleep=options.sleep||(ms=>new Promise(r=>setTimeout(r,ms))),clock=options.clock||Date.now;
 const localVisionOnly=options.localVisionOnly!==false;
 const unavailableVision=()=>{if(localVisionOnly)throw new MediaError('local_vision_unavailable');return {handled:false,not_dispatched:true};};
 const active=new Set();let closed=false;
 async function request(method,route,body,headers={},binary=false){
  if(closed)throw new MediaError('media_client_closed');const control=new AbortController();active.add(control);const timer=setTimeout(()=>control.abort(),20000);timer.unref?.();
  try{
   const res=await fetcher(BASE+route,{method,redirect:'error',signal:control.signal,headers:{Authorization:'Bearer '+key(),...(body!==undefined&&typeof body==='string'?{'Content-Type':'application/json'}:{}),...headers},body});
   const limit=binary?16*1024*1024:1024*1024,parts=[];let size=0;
   for await(const part of res.body){size+=part.byteLength;if(size>limit){control.abort();throw new MediaError('media_response_limit',method==='POST');}parts.push(Buffer.from(part));}
   const raw=Buffer.concat(parts);if(!res.ok){let code='media_request_failed';try{const p=JSON.parse(raw);if(/^[a-z_]{1,80}$/.test(p?.error?.code))code=p.error.code;}catch{}throw new MediaError(code,method==='POST'&&res.status>=500);}
   if(binary){if(!/^image\/(png|jpeg|webp)(?:;|$)/.test(res.headers.get('content-type')||''))throw new MediaError('invalid_media_result');return raw;}
   const value=JSON.parse(raw);if(!value||typeof value!=='object'||Array.isArray(value))throw new MediaError('invalid_media_result',method==='POST');return value;
  }catch(e){if(e instanceof MediaError)throw e;throw new MediaError(method==='POST'?'media_submission_unknown':'media_unavailable',method==='POST');}
  finally{clearTimeout(timer);active.delete(control);}
 }
 async function capabilities(){return request('GET','/capabilities');}
 async function render(kind,prompt,references,logicalID){
  if(typeof prompt!=='string'||!prompt.trim()||Buffer.byteLength(prompt)>12000)throw new MediaError('invalid_prompt');
  let caps;try{caps=await capabilities();}catch(e){if(['media_unavailable','media_bridge_unavailable'].includes(e.code))return {handled:false,not_dispatched:true};throw e;}
  const supported=kind==='generate'?caps.features?.image_generation:caps.features?.image_analysis;
  if(!supported)return {handled:false,not_dispatched:true};
  if(!caps.queue_ready)throw new MediaError('media_queue_blocked');
  const idem=crypto.createHash('sha256').update(logicalID).digest('hex');
  const response=await request('POST','/jobs',JSON.stringify({kind,prompt,size:'512x512',reference_file_ids:references}),{'Idempotency-Key':idem});
  let job=response.job;const deadline=clock()+150000;
  if(!/^job_[A-Za-z0-9_-]{1,128}$/.test(job?.job_id||''))throw new MediaError('media_submission_unknown',true);
  const id=job.job_id;
  while(!['completed','failed','cancelled','unknown','cancel_requested'].includes(job.status)){
   if(closed)throw new MediaError('media_client_closed',true);
   if(clock()>deadline)throw new MediaError('media_job_pending',true);
   await sleep(500);let value;try{value=await request('GET','/jobs/'+id);}catch(e){e.uncertain=true;throw e;}job=value.job;if(job?.job_id!==id)throw new MediaError('media_job_identity_mismatch',true);
  }
  if(job.status!=='completed')throw new MediaError(job.status==='unknown'||job.status==='cancel_requested'?'media_job_uncertain':job.error?.code||'media_job_failed',job.status==='unknown'||job.status==='cancel_requested');
  if(kind==='analyze'){
   if(typeof job.result?.text!=='string'||!job.result.text.trim())throw new MediaError('empty_vision_result');
   return {handled:true,ok:true,status:'ok',provider:'Harmonika Ai',analysis:job.result.text,processing_mode:'local_only',analysis_performed:true,content_is_untrusted:true};
  }
  const artifact=job.result?.artifacts?.[0];if(!/^file_[A-Za-z0-9_-]{1,128}$/.test(artifact?.file_id||'')||artifact.mime_type!=='image/png'||!Number.isSafeInteger(artifact.size_bytes)||artifact.size_bytes<=0||artifact.size_bytes>16*1024*1024||!/^([a-f0-9]{64})$/.test(artifact.sha256||''))throw new MediaError('invalid_image_artifact');
  const data=await request('GET','/files/'+artifact.file_id,undefined,{},true);
  if(data.length!==artifact.size_bytes||crypto.createHash('sha256').update(data).digest('hex')!==artifact.sha256)throw new MediaError('image_checksum_mismatch');
  return {handled:true,ok:true,status:'ok',provider:'Harmonika Ai',mime_type:'image/png',image_base64:data.toString('base64'),width:artifact.width,height:artifact.height};
 }
 return {capabilities,
  generate:(prompt,output)=>render('generate',prompt,[],'generate\0'+path.resolve(output)+'\0'+prompt),
  async analyze(base64,prompt){
   let caps;try{caps=await capabilities();}catch(e){if(['media_unavailable','media_bridge_unavailable'].includes(e.code))return unavailableVision();throw e;}if(!caps.features?.image_analysis)return unavailableVision();
   if(typeof base64!=='string'||base64.length>14*1024*1024||! /^[A-Za-z0-9+/]*={0,2}$/.test(base64))throw new MediaError('invalid_image_input');
   const bytes=Buffer.from(base64,'base64');if(bytes.length>10*1024*1024)throw new MediaError('image_input_limit');
   const form=new FormData();form.append('file',new Blob([bytes]),'reference.png');const uploaded=await request('POST','/files',form);
   if(!/^file_[A-Za-z0-9_-]{1,128}$/.test(uploaded.file?.file_id||''))throw new MediaError('invalid_reference_file');
   const result=await render('analyze',prompt,[uploaded.file.file_id],'analyze\0'+crypto.createHash('sha256').update(bytes).digest('hex')+'\0'+prompt+'\0'+uploaded.file.file_id);
   if(result?.not_dispatched===true&&localVisionOnly)return unavailableVision();
   return result;
  },close(){closed=true;for(const control of active)control.abort();active.clear();}}
}
module.exports={createMediaBridge,MediaError,BASE};
