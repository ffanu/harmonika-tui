'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
let {createMonitor,classifyConnection,backoff,actionRows,text}={};
test.before(async()=>({createMonitor,classifyConnection,backoff,actionRows,text}=await import('../runtime/tui-control/model.mjs')));
const event=(type,data={})=>({type,data:Object.defineProperties({sessionID:'ses_synthetic'},Object.getOwnPropertyDescriptors(data))});
test('observes actual session and tool transitions without inventing completed steps',()=>{
 const m=createMonitor(()=>100);
 assert.equal(m.get('ses_synthetic',true).status,'running');assert.equal(m.get('ses_synthetic').observed,false);
 m.accept(event('session.execution.started'));
 m.accept(event('session.tool.input.started',{id:'call_1',name:'network_exec'}));
 m.accept(event('session.tool.called',{id:'call_1',executed:true,input:{command:'PRIVATE_SENTINEL'}}));
 assert.equal(m.get('ses_synthetic').status,'tool_running');
 m.accept(event('session.tool.success',{id:'call_1',executed:true,content:'PRIVATE_SENTINEL'}));
 m.accept(event('session.execution.succeeded'));
 const s=m.get('ses_synthetic');assert.equal(s.status,'completed');assert.equal(s.tools[0].provider_executed,true);assert.ok(!JSON.stringify(s).includes('PRIVATE_SENTINEL'));
});
test('tool interruption requires reconciliation and never claims the tool stopped or succeeded',()=>{
 const m=createMonitor();m.accept(event('session.execution.started'));m.accept(event('session.tool.input.started',{id:'call_1',name:'deploy'}));m.accept(event('session.tool.called',{id:'call_1',executed:true}));m.accept(event('session.execution.interrupted'));
 assert.equal(m.get('ses_synthetic').tools[0].status,'recovery_required');assert.equal(m.get('ses_synthetic').status,'interrupted');
});
test('partial response plus failure/retry remains visibly interrupted',()=>{
 const m=createMonitor();m.accept(event('session.execution.started'));m.accept(event('session.text.delta',{get delta(){throw Error('must not read private delta')}}));m.accept(event('session.retry.scheduled',{attempt:3,at:500}));assert.equal(m.get('ses_synthetic').status,'interrupted');
 m.accept(event('session.execution.failed',{error:{type:'provider.network',get message(){throw Error('must not read private error')}}}));assert.equal(m.get('ses_synthetic').status,'interrupted');
});
test('retry/permission/input/compaction are distinct mechanical states',()=>{
 const m=createMonitor();m.accept(event('session.retry.scheduled',{attempt:2,at:100}));assert.equal(m.get('ses_synthetic').status,'retrying');
 m.accept(event('permission.asked'));assert.equal(m.get('ses_synthetic').status,'waiting_permission');m.accept(event('permission.replied'));assert.equal(m.get('ses_synthetic').status,'running');
 m.accept(event('form.created'));assert.equal(m.get('ses_synthetic').status,'waiting_input');m.accept(event('form.replied'));assert.equal(m.get('ses_synthetic').status,'running');
 m.accept(event('session.compaction.started'));assert.equal(m.get('ses_synthetic').compacting,true);m.accept(event('session.compaction.ended'));assert.equal(m.get('ses_synthetic').compacting,false);
});
test('no raw messages, titles, reasoning, arbitrary metadata or unbounded event retention',()=>{
 const m=createMonitor();assert.equal(m.accept(event('session.reasoning.delta',{delta:'PRIVATE_SENTINEL'})),false);
 for(let i=0;i<100;i++)m.accept({type:'session.execution.started',data:{sessionID:'ses_'+i,title:'PRIVATE_SENTINEL'}});
 assert.equal(m.get('ses_0').observed,false);assert.equal(m.get('ses_99').observed,true);
 for(let i=0;i<100;i++)m.accept(event('session.tool.input.started',{id:'call_'+i,name:'read_file'}));assert.equal(m.get('ses_synthetic').tools.length,40);
 const s=m.get('ses_synthetic');s.tools.length=0;assert.equal(m.get('ses_synthetic').tools.length,40);
});
test('auth, quota and interrupted streams never invite blind automatic resubmission',()=>{
 assert.equal(classifyConnection({backend:{status:'auth_required'}},{}).retry,false);
 assert.equal(classifyConnection({backend:{status:'online'}},{error_type:'provider.auth'}).state,'auth_required');
 assert.equal(classifyConnection({backend:{status:'online'}},{error_type:'provider.rate-limit'}).state,'limited');
 assert.equal(classifyConnection({backend:{status:'online'}},{status:'interrupted'}).retry,false);
 assert.equal(classifyConnection({backend:{status:'offline'}},{}).retry,true);
 assert.equal(backoff(0),15000);assert.equal(backoff(100),60000);
});
test('terminal strings cannot inject ANSI, newlines, bidi controls or huge values',()=>{
 assert.equal(text('\x1b[31mhello\n\u202e',10),'[31mhello');assert.equal(text('x'.repeat(300)).length,100);
});
test('explicit action rows avoid wrapped footer overlap on narrow terminals',()=>{
 const actions=['Segarkan','Cari','Baca key','Simpan','Hapus proyek','Periksa perubahan'].map(label=>({label}));
 for(const width of [36,54,80,120]) {
  const rows=actionRows(actions,width);assert.deepEqual(rows.flat(),actions);
  for(const row of rows)assert.ok(row.reduce((n,item)=>n+item.label.length+2,0)+row.length-1<=width-2);
 }
});
test('exit status only from explicit mechanics; late tool success does not undo interruption',()=>{
 const m=createMonitor();m.accept(event('session.execution.started'));m.accept(event('session.tool.input.started',{id:'call_1',name:'test_runner'}));m.accept(event('session.execution.interrupted'));
 m.accept(event('session.tool.success',{id:'call_1',executed:true,metadata:{exit_code:1,log:'PRIVATE_SENTINEL'}}));const s=m.get('ses_synthetic');assert.equal(s.status,'interrupted');assert.equal(s.tools[0].exit_code,1);assert.ok(!JSON.stringify(s).includes('PRIVATE_SENTINEL'));
});
test('local canonical tool success is complete even when provider-hosted execution is false',()=>{
 const m=createMonitor();m.accept(event('session.tool.input.started',{id:'local_1',name:'execute'}));m.accept(event('session.tool.called',{id:'local_1',executed:false}));m.accept(event('session.tool.success',{id:'local_1',executed:false,metadata:{exit_code:0}}));
 const tool=m.get('ses_synthetic').tools[0];assert.equal(tool.status,'completed');assert.equal(tool.provider_executed,false);assert.equal(tool.exit_code,0);
});
