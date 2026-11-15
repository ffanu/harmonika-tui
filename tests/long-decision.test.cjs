'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const {PassThrough,Writable}=require('node:stream');
const transport=require('../backend/planner/long-decision.cjs');
const capacity={model:'gpt-6.1-sol',context_window_tokens:872000,auto_compact_tokens:780000};
function fixture(options={}){
  const requests=[],cli=[],children=[];
  function spawn(bin,args,config){
    cli.push({bin,args,config});
    const child=new EventEmitter();children.push(child);child.stdout=new PassThrough();child.stderr=new PassThrough();
    let buffer='';
    function reply(value){
      const data=Buffer.from(JSON.stringify(value)+'\n');
      // Exercise UTF-8 boundaries, including Arabic and emoji inside JSON text.
      const at=options.fragmentUtf8?data.indexOf(Buffer.from('مرحبا'))+1:0;
      if(at>0){child.stdout.write(data.subarray(0,at));child.stdout.write(data.subarray(at));}else child.stdout.write(data);
    }
    const event=(method,params)=>reply({method,params});
    child.stdin=new Writable({write(data,encoding,done){
      buffer+=data.toString('utf8');let at;
      while((at=buffer.indexOf('\n'))!==-1){
        const line=buffer.slice(0,at);buffer=buffer.slice(at+1);const r=JSON.parse(line);requests.push(r);
        if(r.method==='initialize')queueMicrotask(()=>reply({id:r.id,result:{}}));
        if(r.method==='thread/start')queueMicrotask(()=>reply({id:r.id,result:{thread:{id:'synthetic-thread',ephemeral:options.persistent!==true}}}));
        if(r.method==='thread/inject_items')queueMicrotask(()=>reply({id:r.id,...(options.injectError?{error:{code:-32602,message:'synthetic error'}}:{result:{}})}));
        if(r.method==='turn/start')queueMicrotask(()=>{
          if(options.unexpectedTool){reply({id:999,method:'item/tool/call',params:{private:'synthetic-private-value'}});return;}
          reply({id:r.id,result:{turn:{id:'synthetic-turn',status:'inProgress'}}});
          if(options.hang)return;
          if(options.reroute){event('model/rerouted',{});return;}
          const answer=options.answer||JSON.stringify({content:'مرحبا 🌊',tool_calls:[]});
          event('item/agentMessage/delta',{itemId:'answer',delta:answer});
          event('item/completed',{item:{type:'agentMessage',phase:'final_answer',text:answer}});
          event('turn/completed',{turn:{id:'synthetic-turn',status:options.contextError?'failed':'completed',error:options.contextError?{codexErrorInfo:'contextWindowExceeded',message:'synthetic private diagnostic'}:null}});
        });
      }
      done();
    },final(done){done();if(!options.noClose)queueMicrotask(()=>child.emit('close',0,null));}});
    child.kill=()=>{throw Error('must_not_signal_process');};
    return child;
  }
  return {spawn,requests,cli,children};
}
const input=overrides=>({instructions:'Return schema only. No tools.',payload:'synthetic envelope',schema:{type:'object'},capacity,cwd:'/tmp/synthetic-only',...overrides});
test('routes by actual UTF-8 envelope size before reaching CLI single-message ceiling',()=>{
  assert.equal(transport.needsHistory('x'.repeat(900000)),false);
  assert.equal(transport.needsHistory('x'.repeat(900001)),true);
  assert.equal(transport.needsHistory('界'.repeat(310000)),true);
});
test('source-writing client schemas use the cancellable protocol even for a small prompt',()=>{
  for(const content of ['content','newString','new_text']){
    const data={tools:[{type:'function',function:{name:'opaque',parameters:{type:'object',properties:{path:{type:'string'},[content]:{type:'string'}}}}}]};
    assert.equal(transport.needsProtocol(data,'small input'),true);
  }
  assert.equal(transport.needsProtocol({tools:[{function:{parameters:{properties:{commands:{type:'array'}}}}}]},'small input'),false);
  assert.equal(transport.needsProtocol({},'x'.repeat(900001)),true);
});
test('chunking is lossless at surrogate boundaries and bounds each UTF-8 message',()=>{
  const value='x'.repeat(159999)+'🌊'+'界'.repeat(400000);
  const parts=transport.chunks(value);
  assert.equal(parts.join(''),value);
  for(const part of parts){assert.ok(Buffer.byteLength(part)<700000);assert.doesNotMatch(part,/^[\uDC00-\uDFFF]|[\uD800-\uDBFF]$/);}
});
test('large envelope is injected losslessly and the generating turn stays small',async()=>{
  const f=fixture({fragmentUtf8:true}),payload=JSON.stringify({data:'fixture '.repeat(150000)});
  const answer=await transport.run(input({payload}),f);
  assert.equal(JSON.parse(answer).content,'مرحبا 🌊');
  const injected=f.requests.find(r=>r.method==='thread/inject_items').params.items;
  assert.equal(injected.map(i=>i.content[0].text).join(''),payload);
  assert.ok(injected.length>1);
  assert.ok(f.requests.find(r=>r.method==='turn/start').params.input[0].text.length<1000);
  const thread=f.requests.find(r=>r.method==='thread/start').params;
  assert.equal(thread.ephemeral,true);assert.equal(thread.allowProviderModelFallback,false);
  assert.equal(thread.sandbox,'read-only');assert.equal(thread.approvalPolicy,'never');
  assert.ok(f.cli[0].args.includes('mcp_servers.google-ai.enabled=false'));
  assert.ok(f.cli[0].args.includes('features.shell_tool=false'));
});
test('refuses persistent history instead of storing a private conversation',async()=>{
  const f=fixture({persistent:true});
  await assert.rejects(transport.run(input(),f),/tool_engine_ephemeral_required/);
  assert.ok(!f.requests.some(r=>r.method==='thread/inject_items'));
});
test('context exhaustion remains a size error without leaking provider diagnostics',async()=>{
  const f=fixture({contextError:true});
  await assert.rejects(transport.run(input(),f),e=>e.message==='tool_input_too_large'&&!e.message.includes('private'));
});
test('rejects tool/approval requests and model rerouting without a client executor',async()=>{
  for(const options of [{unexpectedTool:true},{reroute:true}]){
    const f=fixture(options);
    await assert.rejects(transport.run(input(),f),options.unexpectedTool?/tool_engine_unexpected_tool/:/tool_engine_model_changed/);
  }
});
test('deadline cancels only its own turn through protocol, without sending process signals',async()=>{
  const f=fixture({hang:true});
  await assert.rejects(transport.run(input({timeoutMs:20}),f),/tool_engine_timeout/);
  const interrupt=f.requests.find(r=>r.method==='turn/interrupt');
  assert.equal(interrupt.params.threadId,'synthetic-thread');assert.equal(interrupt.params.turnId,'synthetic-turn');
});
test('injection protocol failure does not replay generation or expose raw errors',async()=>{
  const f=fixture({injectError:true});
  await assert.rejects(transport.run(input(),f),/^Error: tool_engine_protocol_error$/);
  assert.ok(!f.requests.some(r=>r.method==='turn/start'));
});
test('timeout is retryable only after the decision process has closed, with no returned tool',async()=>{
  const f=fixture({hang:true});let closures=0;
  await assert.rejects(transport.run(input({timeoutMs:10,onClose(){closures++;}}),f),e=>e.message==='tool_engine_timeout'&&e.decisionUnexecuted===true&&e.transportClosed===true);
  assert.equal(closures,1);assert.equal(f.requests.filter(x=>x.method==='turn/start').length,1);
  f.children[0].emit('close',0,null);assert.equal(closures,1);
});
test('unclosed timed-out process cannot release admission or authorize retry; late output stays ignored',async()=>{
  const f=fixture({hang:true,noClose:true});let closures=0;
  await assert.rejects(transport.run(input({timeoutMs:10,closeWaitMs:10,onClose(){closures++;}}),f),e=>e.message==='tool_engine_timeout'&&e.transportClosed!==true);
  assert.equal(closures,0);
  f.children[0].stdout.write(JSON.stringify({method:'item/completed',params:{item:{type:'agentMessage',text:'late private fixture'}}})+'\n');
  f.children[0].emit('close',0,null);assert.equal(closures,1);
});
test('synchronous spawn failure releases only the unstarted transport resource',async()=>{
  let closures=0;
  await assert.rejects(transport.run(input({onClose(){closures++;}}),{spawn(){throw Error('private diagnostic');}}),/^Error: tool_engine_unavailable$/);
  assert.equal(closures,1);
});
test('output overflow is retryable only after its unexecuted process closes',async()=>{
 const f=fixture({answer:'x'.repeat(300000)});let closures=0;
 await assert.rejects(transport.run(input({closeWaitMs:20,onClose(){closures++;}}),f),e=>e.message==='tool_engine_output_limit'&&e.decisionUnexecuted===true&&e.transportClosed===true);
 assert.equal(closures,1);assert.equal(f.requests.filter(x=>x.method==='turn/start').length,1);
});
test('unclosed overflowing process cannot authorize recovery or release its slot',async()=>{
 const f=fixture({answer:'x'.repeat(300000),noClose:true});let closures=0;
 await assert.rejects(transport.run(input({closeWaitMs:10,onClose(){closures++;}}),f),e=>e.message==='tool_engine_output_limit'&&e.transportClosed!==true);
 assert.equal(closures,0);f.children[0].emit('close',0,null);assert.equal(closures,1);
});
