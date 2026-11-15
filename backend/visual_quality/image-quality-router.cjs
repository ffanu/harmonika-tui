'use strict';
// Shared Python policy; no model credentials, raw observations or prompts in logs.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');const {randomUUID}=require('node:crypto');
const birds=/\b(?:burung|birds?|macaws?|parrots?|eagles?|owls?|elang|kakatua|beo|merpati)\b/i;
function run(body){
 return new Promise(resolve=>{
  const child=spawn('/usr/bin/python3',['/opt/harmonika-ai/image-quality/image_generation.py'],{
   env:{PATH:process.env.PATH,LANG:process.env.LANG,HOME:'/root'},stdio:['pipe','pipe','ignore']});
  let output='',oversized=false,settled=false;
  const finish=value=>{if(!settled){settled=true;resolve(value)}};
  child.stdout.on('data',chunk=>{if(output.length+chunk.length>262144)oversized=true;else output+=chunk.toString('utf8')});
  child.stdin.on('error',()=>{});child.on('error',()=>finish({ok:false,error:'image_quality_transport_unavailable'}));
  child.on('close',code=>{try{if(code!==0||oversized)throw Error();finish(JSON.parse(output))}catch{finish({ok:false,error:'image_quality_transport_unavailable'})}});
  child.stdin.end(JSON.stringify(body));
 });
}
function wrap(original,{outputRoot='/fast/output/media',publicHost='http://127.0.0.1:17890',logger=()=>{},validatePNG,worker=run}={}){
 let active=false;
 return async(prompt,references=[])=>{
  if(!birds.test(String(prompt))||references.length)return original(prompt,references);
  if(active)return {code:1,stdout:'',stderr:'image_quality_worker_busy'};
  active=true;let temporary;
  try{
   temporary=fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-image-quality-'));fs.chmodSync(temporary,0o700);
   const result=await worker({input:{prompt:String(prompt).trim().slice(0,4000),width:2048,height:2048},output_dir:temporary});
   if(result.ok!==true||!['passed','not_applicable'].includes(result.quality_gate?.status)){
    logger({event:'image_route',provider:'rtx',status:'quality_not_passed'});
    const error=typeof result.error==='string'&&/^[a-z_]{1,80}$/.test(result.error)?result.error:'image_visual_review_unavailable';
    return {code:1,stdout:'',stderr:error};
   }
   const file=fs.realpathSync(result.file),root=fs.realpathSync(temporary);
   if(!file.startsWith(root+path.sep)||fs.statSync(file).size>16*1024*1024)throw Error('invalid_quality_artifact');
   const image=fs.readFileSync(file);if(image.length<2*1024*1024)throw Error('image_quality_not_met');
   const dimensions=validatePNG(image);const id='rtx-image-'+randomUUID(),dir=path.join(outputRoot,id);
   fs.mkdirSync(dir,{recursive:true,mode:0o750});
   try{
    fs.writeFileSync(path.join(dir,'poster.png'),image,{flag:'wx',mode:0o640});
    fs.writeFileSync(path.join(dir,'manifest.json'),JSON.stringify({kind:'image',prompt:String(prompt).trim().slice(0,4000),image_provider_ok:true,
      provider:'rtx-local',model:result.model,...dimensions,files:['poster.png'],quality_verification:result.quality_verification,
      quality_gate:result.quality_gate,generation_attempts:result.generation_attempts}),{flag:'wx',mode:0o640});
   }catch{fs.rmSync(dir,{recursive:true,force:true});throw Error('image_storage_unavailable')}
   const base=/^https?:\/\//i.test(publicHost)?publicHost.replace(/\/$/,''):'https://'+publicHost;
   logger({event:'image_route',provider:'rtx',status:'visual_checked'});
   return {code:0,stdout:`Harmonika Media\nType: image\nOutput: ${dir}\nView: ${base}/media/${id}\nFiles:\n- ${dir}/poster.png\n- ${dir}/manifest.json\n`,stderr:''};
  }catch{return {code:1,stdout:'',stderr:'image_quality_result_unavailable'}}
  finally{if(temporary)fs.rmSync(temporary,{recursive:true,force:true});active=false}
 };
}
module.exports={wrap};
