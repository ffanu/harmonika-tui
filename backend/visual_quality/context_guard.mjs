import window from './context_window.cjs';
import {writeFile,rename} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
export default {
 id:'harmonika.visual-context-guard',
 async setup(ctx){
  const hook=await ctx.session.hook('http.request',async event=>{
   const request=event.request;
   if(request.method!=='POST'||!request.headers.get('content-type')?.includes('application/json'))return;
   const raw=await request.clone().text();if(raw.length>8*1024*1024)return;
   let value;try{value=JSON.parse(raw)}catch{return}
   const projected=window.project(value);
   // Mechanics only, no message text, prompt, image, key, session ID or raw exception.
   try{
    const messages=value.messages??[];let latest=messages.findLastIndex(m=>m.role==='user');
    const state={timestamp:new Date().toISOString(),input_chars:raw.length,output_chars:JSON.stringify(projected).length,message_count:messages.length,system_chars:JSON.stringify(messages.filter(m=>['system','developer'].includes(m.role))).length,tool_schema_chars:JSON.stringify(value.tools??[]).length,current_turn_chars:JSON.stringify(messages.slice(Math.max(0,latest))).length,projection_applied:projected!==value,role_sizes:messages.reduce((out,m)=>{const r=out[m.role]??={count:0,chars:0,max:0};const n=JSON.stringify(m).length;r.count++;r.chars+=n;r.max=Math.max(r.max,n);return out},{})};
    const target=new URL('./projection-state.json',import.meta.url);const tmp=new URL('./.projection-'+randomUUID()+'.tmp',import.meta.url);
    await writeFile(tmp,JSON.stringify(state),{mode:0o600});await rename(tmp,target);
   }catch{}
   if(projected===value)return;
   const headers=new Headers(request.headers);headers.delete('content-length');
   event.request=new Request(request,{headers,body:JSON.stringify(projected)});
  },{providerID:'harmonika'});
  return ()=>hook.dispose();
 }
};
