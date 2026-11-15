const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createMonitor,toolPhase}=require('../runtime/tui-control/model.mjs');
const ev=(type,data={})=>({type,data:Object.defineProperties({sessionID:'ses_1'},Object.getOwnPropertyDescriptors(data))});
test('phase inference uses tool names; opaque execute/shell stay unknown',()=>{
 assert.equal(toolPhase('write'),'write');assert.equal(toolPhase('pkg.install'),'install');assert.equal(toolPhase('build.run'),'build');assert.equal(toolPhase('test.run'),'test');
 assert.equal(toolPhase('shell'),null);assert.equal(toolPhase('execute'),null);assert.equal(toolPhase('test-customer-personal-name'),null);
});
test('phase declarations are explicit, bounded, and never mark build/test passed',()=>{
 const m=createMonitor();m.accept(ev('session.execution.started'));assert.equal(m.declarePhase('ses_1','build'),true);
 assert.equal(m.get('ses_1').phases.find(x=>x.id==='build').status,'declared');
 assert.equal(m.declarePhase('ses_1','finish'),false);assert.equal(m.declarePhase('ses_1','PRIVATE_SENTINEL'),false);
 m.accept(ev('session.execution.succeeded'));
 assert.equal(m.get('ses_1').phases.find(x=>x.id==='test').status,'not_observed');
 assert.equal(m.get('ses_1').active_phase,'finish');
});
test('no tool input/output inspection; failed command exit is visible, not all tests passed',()=>{
 const m=createMonitor();m.accept(ev('session.tool.input.started',{id:'call_1',name:'test.run'}));
 m.accept(ev('session.tool.called',{id:'call_1',get input(){throw Error('private input read')}}));
 m.accept(ev('session.tool.success',{id:'call_1',get content(){throw Error('private output read')},metadata:{exit_code:1,get log(){throw Error('private log read')}}}));
 assert.equal(m.get('ses_1').phases.find(x=>x.id==='test').last_exit_code,1);
 assert.equal(m.get('ses_1').phases.find(x=>x.id==='test').status,'observed');
});
test('new execution resets phases/tools; new model step resets partial flags only',()=>{
 const m=createMonitor();m.accept(ev('session.execution.started'));m.declarePhase('ses_1','build');
 m.accept(ev('session.text.delta',{get delta(){throw Error('private delta read')}}));assert.equal(m.get('ses_1').step_emitted,true);
 m.accept(ev('session.step.started'));assert.equal(m.get('ses_1').step_emitted,false);assert.equal(m.get('ses_1').phases.find(x=>x.id==='build').status,'declared');
 m.accept(ev('session.execution.started'));assert.equal(m.get('ses_1').phases.find(x=>x.id==='build').status,'not_observed');
});
test('snapshot copies metadata and separates sessions',()=>{
 const m=createMonitor();m.declarePhase('ses_1','install');const s=m.get('ses_1');s.phases[0].status='PRIVATE_SENTINEL';
 assert.ok(!JSON.stringify(m.get('ses_1')).includes('PRIVATE_SENTINEL'));assert.equal(m.get('ses_other').observed,false);
});
test('actual runtime registers scoped retry hook, pure phase tool and read-only RPC',async()=>{
 const runtime=(await import('../runtime/tui-control/runtime.mjs')).default;
 const hooks={},handlers={},tools=[];let disposed=0,finish;
 const eventPromise=new Promise(resolve=>finish=resolve);
 const iterator={next:()=>eventPromise,return:async()=>{finish({done:true});return {done:true}}};
 const registration=()=>({dispose:async()=>{disposed++}});
 const ctx={rpc:{register:async(d,h)=>{Object.assign(handlers,h);return registration()}},session:{hook:async(n,f,options)=>{hooks[n]=f;assert.equal(options.providerID,'harmonika');return registration()}},tool:{transform:async(f)=>{f({add:t=>tools.push(t)});return registration()}},event:{subscribe:()=>({[Symbol.asyncIterator]:()=>iterator})}};
 const cleanup=await runtime.setup(ctx);
 await tools[0].execute({phase:'install'},{sessionID:'ses_1'});
 const e={sessionID:'ses_1',attempt:2,error:{status:503,response:{body:'{"error":{"code":"invalid_tools"}}'}},decision:{retry:true,delay:100}};
 hooks.retry(e);assert.equal(e.decision.retry,false);
 await assert.rejects(handlers.get({sessionID:'../invalid'}),/invalid_session_id/);
 const result=await handlers.get({sessionID:'ses_1'});assert.equal(result.retry.reason,'invalid_arguments');assert.equal(result.active_phase,'install');
 await assert.rejects(tools[0].execute({phase:'finish'},{sessionID:'ses_1'}),/invalid_workflow_phase/);
 assert.ok(!tools[0].description.includes('PRIVATE_SENTINEL'));await cleanup();assert.equal(disposed,3);
});
