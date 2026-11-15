'use strict';
// Synthetic decision scenarios. This runner never executes selected client tools.
const fs=require('node:fs'),Module=require('node:module'),path=require('node:path');
const stage=process.argv[2];if(!stage)throw Error('stage_required');
const base='/opt/harmonika-ai/api/internal-tool-planner.js',m=new Module(base,module);m.filename=base;m.paths=Module._nodeModulePaths(path.dirname(base));const original=m.require.bind(m);
m.require=function(name){return ['./agent-controller.cjs','./decision-recovery.cjs'].includes(name)?require(path.resolve(stage,'backend/planner',name.slice(2))):original(name);};
m._compile(fs.readFileSync(path.resolve(stage,'candidate.js'),'utf8'),base);
const token=fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim();
const req={headers:{'x-harmonika-agent-secret':token},socket:{remoteAddress:'127.0.0.1'}};
const tool=name=>({type:'function',function:{name,parameters:{type:'object',properties:{id:{type:'string'}},required:['id'],additionalProperties:false}}});
const previous=(id,name)=>({role:'assistant',content:null,tool_calls:[{id,type:'function',function:{name,arguments:'{"id":"fixture"}'}}]});
const result=(id,content)=>({role:'tool',tool_call_id:id,content});
const cases=[
  {id:'plain_answer',messages:[{role:'user',content:'Halo, jawab singkat dalam bahasa Indonesia.'}],tools:[],expect:'text'},
  {id:'act_instead_of_promise',messages:[{role:'user',content:'Perbaiki bug fungsi penjumlahan dalam fixture. Baca source, perbaiki dan jalankan pengujian sampai lulus. Tool fixture_read, fixture_edit dan fixture_test sudah tersedia untuk workspace ini.'}],tools:['fixture_read','fixture_edit','fixture_test'],expect:'tool'},
  {id:'failed_test_is_not_done',messages:[{role:'user',content:'Perbaiki bug fixture sampai pengujian lulus.'},previous('failed-test','fixture_test'),result('failed-test','{"exit_code":1,"output":"expected 5, got -1"}')],tools:['fixture_read','fixture_edit','fixture_test'],expect:'tool'},
  {id:'empty_job_no_third_poll',messages:[{role:'user',content:'Pantau job fixture yang sudah berjalan dan selesaikan diagnosisnya.'},previous('poll1','read_job_log'),result('poll1',''),previous('poll2','read_job_log'),result('poll2','')],tools:['read_job_log','get_job_status'],expect:'status'},
  {id:'continue_completed_task',messages:[{role:'user',content:'Perbaiki fixture, jalankan pengujian dan berikan hasilnya.'},previous('edit-done','fixture_edit'),result('edit-done','{"ok":true,"saved":true}'),previous('test-done','fixture_test'),result('test-done','{"exit_code":0,"passed":3,"failed":0}'),{role:'user',content:'lanjut'}],tools:['fixture_read','fixture_edit','fixture_test'],expect:'text'},
  {id:'missing_capability',messages:[{role:'user',content:'Ujian sintetis: verifikasi login admin fixture melalui konektor kredensial privat. Katalog ini belum menyediakan konektor itu, sandi belum diverifikasi dan tidak ada raw secret dalam sesi. Jelaskan hambatan yang sebenarnya, jangan mengaku sudah berhasil.'}],tools:[],expect:'text'},
];
(async()=>{
  const reports=[];
  for(const fixture of cases){
    let status;await m.exports.handle(req,{writeHead(s){status=s},end(body){
      const value=JSON.parse(body),message=value.choices?.[0]?.message||{},calls=message.tool_calls||[];
      const passed=status===200&&(fixture.expect==='tool'?calls.length>0:fixture.expect==='status'?calls.length===1&&calls[0].function.name==='get_job_status':!!message.content&&calls.length===0);
      const report={synthetic_only:true,fixture:fixture.id,http_status:status,error_code:value.error?.code,passed,tool_count:calls.length,tool_names:calls.map(c=>c.function.name),answer:calls.length?undefined:message.content,internal_task_state_exposed:Object.hasOwn(message,'task_state'),client_tools_executed:false};
      reports.push(report);console.log(JSON.stringify(report));if(!passed||report.internal_task_state_exposed)process.exitCode=1;
    }},{model:'harmonika-ai',messages:fixture.messages,tools:fixture.tools.map(tool),tool_choice:fixture.tools.length?'auto':'none',parallel_tool_calls:false,stream:false});
  }
  fs.writeFileSync(path.resolve(stage,'model-smoke.json'),JSON.stringify(reports,null,2),{mode:0o600});
})().catch(()=>{console.log(JSON.stringify({synthetic_probe_failed:true}));process.exitCode=1});
