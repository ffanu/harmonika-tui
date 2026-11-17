// Native chronological system provenance, RAM only. Never infer authority from
// user text, reminder tags, provider headers or tool output.
import {randomBytes} from 'node:crypto';
const PREFIX='HARMONIKA_NATIVE_SYSTEM_UPDATE:';
const escape=text=>text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const wrap=text=>'<system-update>\n'+escape(text)+'\n</system-update>';
export function nativeSystemUpdates({now=Date.now,ttlMs=300000,maxEntries=128,maxBytes=2*1024*1024}={}){
 const records=new Map();let bytes=0,disposed=false,requests=new WeakMap();
 const drop=record=>{if(records.get(record.nonce)!==record)return;records.delete(record.nonce);bytes-=record.bytes;};
 const prune=()=>{const clock=now();for(const record of records.values())if(record.expires<=clock)drop(record);};
 const scoped=event=>event?.kind==='primary'&&typeof event.sessionID==='string'&&event.sessionID.length>0&&event.sessionID.length<=256;
 function register(event){
  if(disposed||!scoped({...event,kind:event?.kind??'primary'})||event.kind&&event.kind!=='primary'||!Array.isArray(event.messages))return 0;
  prune();let count=0;
  const messages=event.messages.map(message=>{
   if(message?.role!=='system'||!Array.isArray(message.content)||!message.content.length||message.content.some(part=>part?.type!=='text'||typeof part.text!=='string'))return message;
   const parts=message.content.map(part=>({...part})),text=parts.map(part=>part.text).join('\n');
   const nonce=randomBytes(32).toString('hex');
   parts[0].text=PREFIX+nonce+'\n'+parts[0].text;
   const wrapped=wrap(parts.map(part=>part.text).join('\n'));
   const cost=Buffer.byteLength(text)+Buffer.byteLength(wrapped);
   if(records.size>=maxEntries||bytes+cost>maxBytes)return message;
   const record={nonce,session:event.sessionID,kind:'primary',wrapped,text,bytes:cost,expires:now()+ttlMs};
   records.set(nonce,record);bytes+=cost;count++;
   return {...message,content:parts};
  });
  if(count)event.messages=messages;
  return count;
 }
 function promote(value,event){
  if(disposed||!scoped(event)||!Array.isArray(value?.messages))return {value,bind(){}};
  prune();const available=[...records.values()].filter(record=>record.session===event.sessionID&&record.kind===event.kind),used=new Set();
  if(!available.length)return {value,bind(){}};
  function split(text){
   const result=[];let offset=0;
   while(offset<text.length){
    let next=null,index=-1;
    for(const record of available){
     if(used.has(record))continue;
     const found=text.indexOf(record.wrapped,offset);
     if(found>=0&&(index<0||found<index)){next=record;index=found;}
    }
    if(!next){result.push({role:'user',text:text.slice(offset)});break;}
    if(index>offset)result.push({role:'user',text:text.slice(offset,index)});
    result.push({role:'developer',text:next.text});used.add(next);offset=index+next.wrapped.length;
   }
   return result;
  }
  const messages=value.messages.flatMap(message=>{
   if(message?.role!=='user')return [message];
   const original=message.content;
   if(typeof original!=='string'&&!Array.isArray(original))return [message];
   const before=used.size;let pending=[],output=[];
   const flush=()=>{if(pending.length){output.push({...message,content:typeof original==='string'?pending.map(p=>p.text).join(''):pending});pending=[];}};
   const parts=typeof original==='string'?[{type:'text',text:original}]:original;
   for(const part of parts){
    if(part?.type!=='text'||typeof part.text!=='string'){pending.push(part);continue;}
    for(const piece of split(part.text)){
     if(piece.role==='developer'){flush();output.push({role:'developer',content:piece.text});}
     else if(piece.text.trim())pending.push({...part,text:piece.text});
    }
   }
   flush();return used.size===before?[message]:output;
  });
  if(!used.size)return {value,bind(){}};
  const lease=[...used].map(record=>record.nonce);
  return {value:{...value,messages},count:lease.length,bind(request){if(request&&typeof request==='object')requests.set(request,lease);}};
 }
 function release(event){
  const status=event?.response?.status;
  if(status===429||status>=500)return; // Native retries may reuse this exact prepared body.
  const lease=requests.get(event?.request);if(!lease)return;requests.delete(event.request);
  for(const nonce of lease){const record=records.get(nonce);if(record)drop(record);}
 }
 function dispose(){disposed=true;records.clear();bytes=0;requests=new WeakMap();}
 function mechanics(){prune();return {records:records.size,bytes};}
 return {register,promote,release,dispose,mechanics};
}
