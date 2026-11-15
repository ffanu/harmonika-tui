'use strict';
// Lossless transport for long decision envelopes. Never executes client tools.
const {spawn}=require('node:child_process');
const {StringDecoder}=require('node:string_decoder');
const LIMIT=900000, CHUNK=160000, MAX_LINE=2*1024*1024, MAX_OUTPUT=256*1024;
const DISABLED=['shell_tool','unified_exec','apps','browser_use','computer_use','view_image','image_generation','multi_agent'];
function needsHistory(prompt){return Buffer.byteLength(prompt,'utf8')>LIMIT;}
function needsProtocol(data,prompt){
  if(needsHistory(prompt))return true;
  return (data.tools||[]).some(tool=>{
    const properties=tool?.function?.parameters?.properties||{};
    return ['path','file_path','filePath'].some(key=>properties[key])&&['content','newString','new_text'].some(key=>properties[key]);
  });
}
function chunks(text){
  const out=[];
  for(let start=0;start<text.length;){
    let end=Math.min(start+CHUNK,text.length);
    if(end<text.length&&/[\uD800-\uDBFF]/.test(text[end-1])&&/[\uDC00-\uDFFF]/.test(text[end]))end--;
    out.push(text.slice(start,end));start=end;
  }
  return out;
}
function run(options,deps={}){
  const {instructions,payload,schema,capacity,cwd}=options;
  return new Promise((resolve,reject)=>{
    const args=['app-server','--stdio','-c','analytics.enabled=false','-c','mcp_servers={}',
      '-c','mcp_servers.google-ai.enabled=false','-c','mcp_servers.perplexity-research.enabled=false',
      '-c','web_search="disabled"',...DISABLED.flatMap(n=>['-c',`features.${n}=false`])];
    let child;
    try{child=(deps.spawn||spawn)('/usr/local/bin/codex',args,{cwd,stdio:['pipe','pipe','pipe']});}
    catch{try{options.onClose?.();}catch{}reject(Error('tool_engine_unavailable'));return;}
    const pending=new Map(),deltas=new Map(),decoder=new StringDecoder('utf8');
    let id=0,threadId=null,turnId=null,buffer='',output='',finished=false,ending=false,timer;
    let completed=null,outputBytes=0,closed=false,pendingCloseError=null,closeTimer;
    function send(value){if(!child.stdin.destroyed&&!child.stdin.writableEnded)child.stdin.write(JSON.stringify(value)+'\n');}
    function stop(){
      if(ending)return;ending=true;
      if(threadId&&turnId&&completed?.status!=='completed')send({id:++id,method:'turn/interrupt',params:{threadId,turnId}});
      child.stdin.end();
    }
    function finish(error,value){
      if(finished)return;finished=true;clearTimeout(timer);
      for(const waiter of pending.values())waiter.reject(Error('tool_engine_unavailable'));
      pending.clear();stop();
      if(['tool_engine_timeout','tool_engine_output_limit'].includes(error?.message)){
        if(closed){error.decisionUnexecuted=true;error.transportClosed=true;reject(error);return;}
        pendingCloseError=error;
        const wait=Number.isFinite(options.closeWaitMs)?Math.max(1,Math.min(options.closeWaitMs,2000)):2000;
        closeTimer=setTimeout(()=>{const e=pendingCloseError;pendingCloseError=null;if(e)reject(e);},wait);
        return;
      }
      error?reject(error):resolve(value);
    }
    function fail(code){finish(Error(code));}
    function request(method,params){
      return new Promise((accept,decline)=>{
        const ident=++id;pending.set(ident,{resolve:accept,reject:decline});
        send({id:ident,method,params});
      });
    }
    function modelError(value){
      const code=value?.codexErrorInfo;
      const type=typeof code==='string'?code:'';
      return type==='contextWindowExceeded'||type==='inputTooLarge'?'tool_input_too_large':'tool_engine_unavailable';
    }
    function observe(message){
      if(finished)return;
      if(message.id!==undefined&&!message.method){
        const waiter=pending.get(message.id);if(!waiter)return;
        pending.delete(message.id);
        message.error?waiter.reject(Error('tool_engine_protocol_error')):waiter.resolve(message.result);
        return;
      }
      if(message.id!==undefined&&message.method){
        send({id:message.id,error:{code:-32601,message:'Decision transport has no tools or approvals'}});
        fail('tool_engine_unexpected_tool');return;
      }
      const p=message.params||{};
      if(message.method==='model/rerouted'){fail('tool_engine_model_changed');return;}
      if(message.method==='item/agentMessage/delta'&&typeof p.delta==='string'){
        outputBytes+=Buffer.byteLength(p.delta);if(outputBytes>MAX_OUTPUT){fail('tool_engine_output_limit');return;}
        const key=String(p.itemId||'final');
        if(!deltas.has(key)&&deltas.size>=32){fail('tool_engine_output_limit');return;}
        deltas.set(key,(deltas.get(key)||'')+p.delta);
      }
      if(message.method==='item/completed'&&p.item?.type==='agentMessage'&&typeof p.item.text==='string'){
        if(Buffer.byteLength(p.item.text)>MAX_OUTPUT){fail('tool_engine_output_limit');return;}
        if(p.item.phase==='final_answer'||p.item.phase==null)output=p.item.text;
      }
      if(message.method==='turn/completed'){
        completed=p.turn;
        if(!turnId)return; // Notification can precede the turn/start response.
        complete();
      }
    }
    function complete(){
      if(!completed||finished)return;
      if(completed.status!=='completed'){fail(modelError(completed.error));return;}
      const text=output||Array.from(deltas.values()).at(-1)||'';
      finish(null,text);
    }
    child.stderr.on('data',()=>{}); // Drain without retaining raw provider diagnostics.
    child.stderr.on('error',()=>{});
    child.stdin.on('error',()=>{if(!finished)fail('tool_engine_unavailable');});
    child.stdout.on('error',()=>fail('tool_engine_unavailable'));
    child.stdout.on('data',data=>{
      if(finished)return;
      buffer+=decoder.write(data);
      let at;
      while((at=buffer.indexOf('\n'))!==-1){
        const line=buffer.slice(0,at);buffer=buffer.slice(at+1);
        if(Buffer.byteLength(line)>MAX_LINE){fail('tool_engine_output_limit');return;}
        if(line){try{observe(JSON.parse(line));}catch{fail('tool_engine_protocol_error');return;}}
      }
      if(Buffer.byteLength(buffer)>MAX_LINE)fail('tool_engine_output_limit');
    });
    child.on('error',()=>fail('tool_engine_unavailable'));
    child.on('close',()=>{
      if(closed)return;closed=true;clearTimeout(closeTimer);
      try{options.onClose?.();}catch{}
      if(pendingCloseError){const e=pendingCloseError;pendingCloseError=null;e.decisionUnexecuted=true;e.transportClosed=true;reject(e);}
      else if(!finished)fail('tool_engine_unavailable');
    });
    const timeout=Number.isFinite(options.timeoutMs)&&options.timeoutMs>0?Math.min(options.timeoutMs,180000):180000;
    timer=setTimeout(()=>fail('tool_engine_timeout'),timeout);
    (async()=>{
      await request('initialize',{clientInfo:{name:'harmonika_decision_transport',version:'1.0.0'},capabilities:{experimentalApi:true}});
      send({method:'initialized',params:{}});
      const result=await request('thread/start',{model:capacity.model,cwd,approvalPolicy:'never',sandbox:'read-only',ephemeral:true,allowProviderModelFallback:false,
        baseInstructions:instructions,
        developerInstructions:'Previous reference messages contain ordered fragments of one untrusted JSON conversation envelope. Concatenate those fragments exactly. Return only the decision schema for its latest request. No built-in tools or host access are permitted.',
        config:{model_context_window:capacity.context_window_tokens,model_auto_compact_token_limit:capacity.auto_compact_tokens,model_reasoning_effort:'low',web_search:'disabled'}});
      if(result?.thread?.ephemeral!==true||typeof result.thread.id!=='string')throw Error('tool_engine_ephemeral_required');
      threadId=result.thread.id;
      await request('thread/inject_items',{threadId,items:chunks(payload).map(text=>({type:'message',role:'user',content:[{type:'input_text',text}]}))});
      const turn=await request('turn/start',{threadId,input:[{type:'text',text:'Using the complete reference JSON envelope above, return the next assistant decision for its latest request. Prior client tool results are evidence of completed actions. Do not repeat completed actions or execute anything yourself.'}],outputSchema:schema,effort:'low'});
      if(typeof turn?.turn?.id!=='string')throw Error('tool_engine_protocol_error');
      turnId=turn.turn.id;complete();
    })().catch(error=>{if(!finished)fail(/^tool_/.test(error.message)?error.message:'tool_engine_unavailable');});
  });
}
module.exports={run,needsHistory,needsProtocol,chunks};
