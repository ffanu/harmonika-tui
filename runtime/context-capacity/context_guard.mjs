import window from './context_window.cjs';
import {open,mkdir,rename,lstat,unlink} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {homedir} from 'node:os';
import path from 'node:path';

const ROLES=new Set(['system','developer','user','assistant','tool','function']);
const MAX_CHARS=8*1024*1024;
export function capacityProfile() {
  try {
    const p=JSON.parse(readFileSync(new URL('./context-capacity.json',import.meta.url),'utf8'));
    if(p.schema_version===1&&p.context_window_tokens===872000&&p.transport_soft_chars===2800000&&p.max_payload_bytes===MAX_CHARS)return p;
  } catch {}
  return undefined;
}
export function projectionMechanics(value,projected,rawLength,clock=()=>new Date().toISOString()) {
  const messages=value.messages;
  let latest=-1;
  for(let i=messages.length-1;i>=0;i--)if(messages[i].role==='user'){latest=i;break}
  const role_sizes={};
  for(const m of messages){
    const role=ROLES.has(m.role)?m.role:'other';
    const row=role_sizes[role]??={count:0,chars:0,max:0};
    const length=JSON.stringify(m).length;row.count++;row.chars+=length;row.max=Math.max(row.max,length);
  }
  return {timestamp:clock(),input_chars:rawLength,output_chars:JSON.stringify(projected).length,message_count:messages.length,
    system_chars:JSON.stringify(messages.filter(m=>['system','developer'].includes(m.role))).length,
    tool_schema_chars:JSON.stringify(value.tools??[]).length,current_turn_chars:JSON.stringify(messages.slice(Math.max(0,latest))).length,
    projection_applied:projected!==value,role_sizes};
}
export function stateDirectory(env=process.env,home=homedir()) {
  const root=env.XDG_STATE_HOME||path.join(home,'.local','state');
  return path.join(root,'harmonika-terminal','context-capacity');
}
export async function recordMechanics(state,env=process.env) {
  // A signed release directory stays immutable. Telemetry is bounded to one
  // private metadata file per OS user and never contains chat, role names from
  // untrusted input, tool output, paths, credentials or session identifiers.
  const directory=stateDirectory(env);
  await mkdir(directory,{recursive:true,mode:0o700});
  const info=await lstat(directory);
  if(!info.isDirectory()||info.isSymbolicLink()||(process.platform!=='win32'&&(info.uid!==process.getuid()||(info.mode&0o077))))throw Error('unsafe_context_state');
  const target=path.join(directory,'projection-state.json');
  const temp=path.join(directory,'.projection-'+randomUUID()+'.tmp');
  try {
    const file=await open(temp,'wx',0o600);
    try{await file.writeFile(JSON.stringify(state));}finally{await file.close();}
    await rename(temp,target);
  } finally {await unlink(temp).catch(()=>{});}
}
export default {
 id:'harmonika.visual-context-guard',
 async setup(ctx){
  const hook=await ctx.session.hook('http.request',async event=>{
   const request=event.request;
   if(request.method!=='POST'||!request.headers.get('content-type')?.includes('application/json'))return;
   const raw=await request.clone().text();if(raw.length>MAX_CHARS)return;
   let value;try{value=JSON.parse(raw)}catch{return}
   if(!value||!Array.isArray(value.messages)||value.messages.some(m=>!m||typeof m!=='object'||Array.isArray(m)))return;
   const projected=window.project(value,{maxChars:capacityProfile()?.transport_soft_chars});
   try{await recordMechanics(projectionMechanics(value,projected,raw.length));}catch{}
   if(projected===value)return;
   const headers=new Headers(request.headers);headers.delete('content-length');
   event.request=new Request(request,{headers,body:JSON.stringify(projected)});
  },{providerID:'harmonika'});
  return ()=>hook.dispose();
 }
};
