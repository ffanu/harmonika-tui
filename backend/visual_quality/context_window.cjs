'use strict';
// Outbound request projection only. The durable session and executor results stay intact.
const {createHash}=require('node:crypto');
const BUDGET=150000;
const size=x=>JSON.stringify(x).length;
const hash=x=>createHash('sha256').update(x).digest('hex');
function latestRequest(messages){
 // Tool-read images can be inserted as image-only user messages by the adapter.
 // They are part of the current task, not a new human instruction boundary.
 let latest=-1,lastUser=-1;
 messages.forEach((m,i)=>{
  if(m.role!=='user')return;
  lastUser=i;
  if(typeof m.content==='string'?m.content.trim():Array.isArray(m.content)&&m.content.some(p=>p?.type==='text'&&typeof p.text==='string'&&p.text.trim()))latest=i;
 });
 return latest<0?lastUser:latest;
}
function excerpt(text,keep=1200){
 return text.slice(0,keep)+'\n[Earlier tool output abbreviated for transport. sha256='+hash(text)+'. Omitted text is NOT execution evidence; reread the original file/result before acting.]\n'+text.slice(-keep);
}
function project(input){
 if(!input||!Array.isArray(input.messages)||size(input)>8*1024*1024)return input;
 // Never clip declarations, instructions, current user requests or tool arguments.
 const data=JSON.parse(JSON.stringify(input));let latest=latestRequest(data.messages);
 if(size(data)<=BUDGET)return input;
 // Old binary attachments are references, not text decision context. Keep user text intact.
 for(let i=0;i<latest;i++){
  const m=data.messages[i];
  if(Array.isArray(m.content))m.content=m.content.map(p=>['image_url','input_image','image','file','input_file','audio','input_audio','video'].includes(p?.type)?{type:'text',text:'[Earlier media omitted from decision transport. Use the original file with the media tool; this is not image analysis.]'}:p);
 }
 for(let i=0;i<latest;i++){
  const m=data.messages[i];
  if(m.role==='tool'&&typeof m.content==='string'&&m.content.length>3000)m.content=excerpt(m.content);
 }
 if(size(data)<=BUDGET)return data;
 // Historical turns remain visible as user requirements and a factual omission marker.
 // Drop complete call/result groups, never isolated tool messages or the current turn.
 let start=0;
 while(start<latest&&size(data)>BUDGET){
  let first=start;
  while(first<latest&&['system','developer'].includes(data.messages[first].role))first++;
  let end=first+1;
  while(end<latest&&data.messages[end].role!=='user'&&!['system','developer'].includes(data.messages[end].role))end++;
  const group=data.messages.slice(first,end);
  if(!group.length||first>=latest)break;
  const users=group.filter(m=>m.role==='user');
  const final=group.filter(m=>m.role==='assistant'&&!m.tool_calls&&typeof m.content==='string').at(-1);
  const summary={role:'assistant',content:'[Earlier tools and intermediate responses omitted from transport; '+hash(JSON.stringify(group))+'. This is not evidence of a new action. Original user requirement retained; inspect existing project artifacts and original tool results before changing scope.]'+(final?'\nPrior answer (historical, verify before relying):\n'+final.content.slice(0,4000):'')};
  const replacement=[...users,summary];
  data.messages.splice(first,end-first,...replacement);
  latest-=group.length-replacement.length;start=first+replacement.length;
 }
 // Keep all current tool results if they fit. Large reads may otherwise fill one entire turn.
 if(size(data)>BUDGET){
  const toolIndexes=data.messages.map((m,i)=>m.role==='tool'?i:-1).filter(i=>i>=latest);
  for(const i of toolIndexes.slice(0,-2)){
   const m=data.messages[i];if(typeof m.content==='string'&&m.content.length>8000)m.content=excerpt(m.content,2000);
   if(size(data)<=BUDGET)break;
  }
 }
 // Mandatory media may exceed the soft target. Preserve it, but do not undo
 // safe historical reductions: reverting here reintroduces old screenshots
 // and can turn a usable current request into an HTTP 413.
 // Backend admission still governs a request whose mandatory content is large.
 return size(data)<size(input)?data:input;
}
module.exports={project,BUDGET};
