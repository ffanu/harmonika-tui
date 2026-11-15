'use strict';
// Reproducible synthetic checker probes. No model, executor or user history.
const path=require('node:path');
const agent=require(path.join(process.argv[2]||path.join(__dirname,'../backend/planner'),'agent-controller.cjs'));
const tap='# tests 2\n# pass 2\n# fail 0\n';
const write=(file,scope={})=>['editor',{path:file,new_text:'fixture'},{ok:true,metadata:scope}];
const test=scope=>['fixture.test',{}, {exit_code:0,collected:2,passed:2,metadata:scope}];
const shell=cwd=>['run_commands',{commands:['cd '+cwd+' && node --test']},[{query:'cd '+cwd+' && node --test',success:true,result:tap}]];
const submit=['fixture.submit',{}, {status:'outcome_unknown',job_id:'job',owner_id:'owner-A',project_id:'project-A'}];
const cases=[
 ['foreign_cwd',[write('/tmp/active/src/main.cjs'),shell('/tmp/archive')]],
 ['sibling_prefix',[write('/tmp/app/src/main.cjs'),shell('/tmp/application')]],
 ['stale_revision',[write('main.cjs',{revision:'new'}),test({revision:'old'})]],
 ['foreign_project',[write('main.cjs',{project_id:'active'}),test({project_id:'archive'})]],
 ['foreign_attempt',[write('main.cjs',{attempt:'current'}),test({attempt:'previous'})]],
 ['job_foreign_owner',[submit,['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',owner_id:'owner-B',project_id:'project-A'}]]],
 ['job_foreign_project',[submit,['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',owner_id:'owner-A',project_id:'project-B'}]]],
 ['one_project_test_for_two_writes',[write('/tmp/active/src/a.cjs'),write('/tmp/other/src/b.cjs'),shell('/tmp/other')]],
 ['stale_readback',[write('main.cjs',{revision:'new'}),['fixture.read',{path:'main.cjs'},{path:'main.cjs',content:'fixture',metadata:{revision:'old'}}]]],
];
const rows=cases.map(([name,entries])=>{
 const prompt=name.startsWith('job_')?'Verifikasi job fixture.':name==='stale_readback'?'Perbaiki file main.cjs.':'Perbaiki bug aplikasi dan jalankan tes.';
 const messages=[{role:'user',content:prompt},...entries.flatMap(([name,args,result],i)=>[
  {role:'assistant',tool_calls:[{id:'r'+i,function:{name,arguments:JSON.stringify(args)}}]},
  {role:'tool',tool_call_id:'r'+i,content:JSON.stringify(result)}])];
 try{agent.check({content:'Pemeriksaan selesai.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:['r'+(entries.length-1)],next_step:''}},{messages});return {name,accepted:true,reason:null};}
 catch(e){return {name,accepted:false,reason:e.decisionReason};}
});
console.log(JSON.stringify({synthetic_only:true,model_called:false,tools_executed:false,rows}));
