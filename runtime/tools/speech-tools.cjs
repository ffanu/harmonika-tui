'use strict';
const fs=require('node:fs'),crypto=require('node:crypto');
const {createClient,VideoError}=require('./video-owner-client.cjs');const {approvedOutput}=require('./video-mcp.cjs');
async function synthesize(args={},options={}){
 const client=options.client||createClient();try{
  if(!args||typeof args!=='object'||Object.keys(args).some(k=>!['text','language','speed','voice','quality','style','output_file'].includes(k)))throw new VideoError('invalid_arguments');
  const {output_file,...input}=args;if(typeof input.text!=='string'||!input.text.trim()||Buffer.byteLength(input.text)>1200)throw new VideoError('invalid_speech_text');
  const out=await client.call('speech_generate',input),raw=Buffer.from(out.audio_base64||'','base64');
  if(out.mime_type!=='audio/wav'||raw.length!==out.bytes||raw.length>5*1024*1024||raw.length<44||raw.subarray(0,4).toString()!=='RIFF'||raw.subarray(8,12).toString()!=='WAVE'||crypto.createHash('sha256').update(raw).digest('hex')!==out.sha256)throw new VideoError('invalid_audio_artifact');
  const dest=approvedOutput(output_file,out.sha256.slice(0,32),{...options,extension:'.wav'});if(fs.existsSync(dest)){if(fs.statSync(dest).size!==raw.length||crypto.createHash('sha256').update(fs.readFileSync(dest)).digest('hex')!==out.sha256)throw new VideoError('output_exists');return {ok:true,status:'ok',provider:'Harmonika Ai',output_file:dest,bytes:raw.length,sha256:out.sha256,duration_seconds:out.duration_seconds,language:out.language,voice_quality:out.voice_quality||'synthetic',voice:out.voice,style:out.style||'normal',native_emotion_control:false,synthetic_audio:true,disclosure:out.disclosure,mime_type:'audio/wav',new_file:false}}let fd;
  try{fd=fs.openSync(dest,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|(fs.constants.O_NOFOLLOW||0),0o600);fs.writeFileSync(fd,raw)}finally{if(fd!==undefined)fs.closeSync(fd)}
  return {ok:true,status:'ok',provider:'Harmonika Ai',output_file:dest,bytes:raw.length,sha256:out.sha256,duration_seconds:out.duration_seconds,language:out.language,voice_quality:out.voice_quality||'synthetic',voice:out.voice,style:out.style||'normal',native_emotion_control:false,synthetic_audio:true,disclosure:out.disclosure,mime_type:'audio/wav'};
 }catch(e){return {ok:false,status:'degraded',provider:'Harmonika Ai',error:{code:e.code||'speech_unavailable'}}}finally{if(!options.client)client.close()}
}
async function capabilities(){const c=createClient();try{return await c.call('speech_capabilities')}finally{c.close()}}
module.exports={synthesize,capabilities};
