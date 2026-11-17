// Bridge native executor mechanics lost by HTTP history lowering. Only hashes
// and an allowlist of typed shell mechanics live in bounded RAM. No tool input,
// output, credential, path or user text is retained or written to disk here.
import {createHash} from 'node:crypto';

const SHELLS=new Set(['shell','bash']);
const STATUSES=new Set(['completed','running','pending','queued','failed','error','cancelled','canceled','timeout','interrupted','unknown']);
const SIGNALS=new Set(['SIGHUP','SIGINT','SIGQUIT','SIGILL','SIGABRT','SIGFPE','SIGKILL','SIGSEGV','SIGPIPE','SIGALRM','SIGTERM','SIGXCPU','SIGXFSZ','SIGBREAK']);
const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
const object=value=>!!value&&typeof value==='object'&&!Array.isArray(value)&&(Object.getPrototypeOf(value)===Object.prototype||Object.getPrototypeOf(value)===null);
const identity=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$/.test(value);
const digest=value=>createHash('sha256').update(value,'utf8').digest('hex');
const key=(session,id)=>JSON.stringify([session,id]);

function argumentsHash(input){
 if(!object(input))return;
 let nodes=0,bytes=0;
 const add=text=>{bytes+=Buffer.byteLength(text);if(bytes>131072)throw Error('argument_budget');return text;};
 const visit=(value,depth)=>{
  if(++nodes>4096||depth>32)throw Error('argument_complexity');
  if(value===null||typeof value==='boolean')return add(JSON.stringify(value));
  if(typeof value==='number'){if(!Number.isFinite(value))throw Error('argument_number');return add(JSON.stringify(value));}
  if(typeof value==='string')return add(JSON.stringify(value));
  if(Array.isArray(value))return '['+value.map(item=>visit(item,depth+1)).join(',')+']';
  if(!object(value))throw Error('argument_type');
  const descriptors=Object.getOwnPropertyDescriptors(value);
  return '{'+Object.keys(descriptors).sort().map(name=>{
   const descriptor=descriptors[name];if(!descriptor.enumerable)return null;
   if(!own(descriptor,'value'))throw Error('argument_accessor');
   return add(JSON.stringify(name))+':'+visit(descriptor.value,depth+1);
  }).filter(item=>item!==null).join(',')+'}';
 };
 const canonical=visit(input,0);if(Buffer.byteLength(canonical)>131072)return;
 return digest(canonical);
}

function nativeText(content,maximum){
 if(typeof content==='string')return Buffer.byteLength(content)<=maximum?content:undefined;
 if(!Array.isArray(content)||content.length>64)return;
 let bytes=0;
 for(const part of content){
  if(!object(part)||part.type!=='text'||typeof part.text!=='string')return;
  bytes+=Buffer.byteLength(part.text);if(bytes>maximum)return;
 }
 if(bytes+Math.max(0,content.length-1)>maximum)return;
 return content.map(part=>part.text).join('\n');
}
function wireText(content){
 if(typeof content==='string')return content;
 if(Array.isArray(content)&&content.length===1&&object(content[0])&&content[0].type==='text'&&typeof content[0].text==='string')return content[0].text;
}
function mechanics(result){
 if(!object(result)||!object(result.metadata))return;
 const original=result.metadata;
 if(typeof original.truncated!=='boolean')return;
 const status=STATUSES.has(original.status)?original.status:'unknown';
 const exits=[];
 if(own(original,'exit'))exits.push(original.exit);
 if(object(result.output)&&own(result.output,'exitCode'))exits.push(result.output.exitCode);
 if(exits.some(value=>value!==null&&(!Number.isSafeInteger(value)||value< -2147483648||value>4294967295)))return;
 if(exits.length>1&&exits.some(value=>value!==exits[0]))return;
 const metadata={status,exit_code:exits.length?exits[0]:null,truncated:original.truncated};
 for(const flag of ['timeout','killed'])if(own(original,flag)){
  if(typeof original[flag]!=='boolean')return;
  metadata[flag]=original[flag];
 }
 if(own(original,'signal')){
  if(!SIGNALS.has(original.signal))return;
  metadata.signal=original.signal;
 }
 return metadata;
}
function boundary(messages){
 let latest=-1,last=-1;
 messages.forEach((message,index)=>{
  if(message?.role!=='user')return;
  last=index;
  if(typeof message.content==='string'?message.content.trim():Array.isArray(message.content)&&message.content.some(part=>part?.type==='text'&&typeof part.text==='string'&&part.text.trim()))latest=index;
 });
 return latest<0?last:latest;
}

export function nativeToolReceipts({now=Date.now,ttlMs=900000,maxEntries=256,maxBytes=262144,maxOutputBytes=1048576}={}){
 const records=new Map();let bytes=0,disposed=false;
 const drop=record=>{if(records.get(record.key)!==record)return;records.delete(record.key);bytes-=record.bytes;};
 const prune=()=>{const clock=now();for(const record of records.values())if(record.expires<=clock)drop(record);};
 function store(record){
  record.expires=now()+ttlMs;record.bytes=0;
  for(let i=0;i<3;i++)record.bytes=Buffer.byteLength(JSON.stringify(record));
  if(record.bytes>maxBytes||maxEntries<1)return false;
  while(records.size>=maxEntries||bytes+record.bytes>maxBytes)drop(records.values().next().value);
  records.set(record.key,record);bytes+=record.bytes;return true;
 }
 function record(event){
  if(disposed||!identity(event?.sessionID)||!identity(event?.id)||!SHELLS.has(event?.tool))return false;
  prune();const id=key(event.sessionID,event.id),previous=records.get(id);
  // Reusing an ID is ambiguous, including two nominally identical executions.
  // Do not replace old execution mechanics with the latest successful-looking one.
  if(previous){drop(previous);store({key:id,blocked:true});return false;}
  try{
   if(event.status!=='completed'){store({key:id,blocked:true});return false;}
   const args=argumentsHash(event.input),text=nativeText(event.result?.content,maxOutputBytes),metadata=mechanics(event.result);
   if(!args||typeof text!=='string'||Buffer.byteLength(text)>maxOutputBytes||!metadata){store({key:id,blocked:true});return false;}
   return store({key:id,name:event.tool,args,content:digest(text),metadata});
  }catch{store({key:id,blocked:true});return false;}
 }
 function promote(value,event){
  if(disposed||event?.kind!=='primary'||event?.model?.providerID!=='harmonika'||!identity(event?.sessionID)||!Array.isArray(value?.messages)||!Array.isArray(value.tools))return {value,count:0};
  prune();const latest=boundary(value.messages);if(latest<0)return {value,count:0};
  const declared=new Map();
  for(const tool of value.tools)if(tool?.type==='function'&&typeof tool.function?.name==='string')declared.set(tool.function.name,(declared.get(tool.function.name)??0)+1);
  const calls=new Map(),results=new Map();
  value.messages.forEach((message,index)=>{
   if(message?.role==='assistant'&&Array.isArray(message.tool_calls))for(const call of message.tool_calls){
    if(!identity(call?.id))continue;
    const existing=calls.get(call.id);calls.set(call.id,existing?{collision:true}:{call,index});
   }
   if(message?.role==='tool'&&identity(message.tool_call_id)){
    const existing=results.get(message.tool_call_id);results.set(message.tool_call_id,existing?{collision:true}:{message,index});
   }
  });
  let messages=value.messages,count=0;
  for(const [id,row] of results){
   const callRow=calls.get(id),cached=records.get(key(event.sessionID,id));
   if(row.collision||!callRow||callRow.collision||!cached||cached.blocked||callRow.index<=latest||row.index<=callRow.index)continue;
   const call=callRow.call,name=call.function?.name;
   if(call.type!=='function'||name!==cached.name||declared.get(name)!==1||(own(row.message,'name')&&row.message.name!==name)||typeof call.function.arguments!=='string')continue;
   try{
    if(argumentsHash(JSON.parse(call.function.arguments))!==cached.args)continue;
    const text=wireText(row.message.content);
    if(typeof text!=='string'||Buffer.byteLength(text)>maxOutputBytes||digest(text)!==cached.content)continue;
    const envelope=JSON.stringify({output:text,metadata:cached.metadata});
    if(messages===value.messages)messages=[...value.messages];
    messages[row.index]={...row.message,content:typeof row.message.content==='string'?envelope:[{...row.message.content[0],text:envelope}]};count++;
   }catch{} // A mismatched or malformed receipt remains ordinary, untrusted content.
  }
  return {value:count?{...value,messages}:value,count};
 }
 function dispose(){disposed=true;records.clear();bytes=0;}
 function counters(){prune();return {records:records.size,bytes};}
 return {record,promote,dispose,counters};
}
