'use strict';
// Synthetic controller-only probes; no model or tool execution.
const path=require('node:path'),agent=require(path.join(process.argv[2]||path.join(__dirname,'../backend/planner'),'agent-controller.cjs'));
const read=['fixture.read',{path:'README.md'},{content:'fixture'}];
const examples=[
 ['unknown_job_state',[['fixture.submit',{}, {status:'in_queue',job_id:'job'}],read]],
 ['missing_job_state',[['fixture.submit',{}, {success:true,job_id:'job'}],read]],
 ['retrying_job_state',[['fixture.submit',{}, {status:'retrying',job_id:'job'}],read]],
 ['unknown_batch_child',[['run_commands',{commands:['fixture-submit','ls']},[{query:'fixture-submit',success:true,result:'accepted',metadata:{status:'in_queue',job_id:'job'}},{query:'ls',success:true,result:'fixture'}]],read]],
 ['partial_test',[['fixture.test',{}, {exit_code:0,collected:2,passed:2,metadata:{truncated:true}}]]],
 ['partial_readback',[['fixture.edit',{path:'main.cjs'},{ok:true}],['fixture.read',{path:'main.cjs'},{path:'main.cjs',content:'part',metadata:{complete:false}}]]],
 ['partial_terminal_status',[['fixture.submit',{}, {status:'outcome_unknown',job_id:'job'}],['fixture.lookup',{job_id:'job'},{status:'completed',job_id:'job',metadata:{complete:false}}]]],
 ['uncertain_full_write_replay',[['fs.write',{path:'/tmp/fixture/file.cjs',content:'fixed'},{ok:true,metadata:{partial:true}}]],'replay'],
];
const rows=examples.map(([name,entries,mode])=>{
 const prompt=name==='partial_test'?'Jalankan tes aplikasi.':name==='partial_readback'?'Perbaiki file main.cjs.':'Verifikasi fixture.';
 const data={messages:[{role:'user',content:prompt},...entries.flatMap(([name,args,result],i)=>[{role:'assistant',tool_calls:[{id:'r'+i,function:{name,arguments:JSON.stringify(args)}}]},{role:'tool',tool_call_id:'r'+i,content:JSON.stringify(result)}])]};
 const value=mode==='replay'?{content:'Ulangi mutasi.',tool_calls:[{name:'fs.write',arguments:'{"path":"/tmp/fixture/file.cjs","content":"fixed"}'}],task_state:{phase:'execute',remaining:['verify'],evidence_call_ids:[],next_step:''}}:{content:'Pemeriksaan selesai.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:['r'+(entries.length-1)],next_step:''}};
 try{agent.check(value,data);return {name,mode:mode||'complete',accepted:true,reason:null};}catch(e){return {name,mode:mode||'complete',accepted:false,reason:e.decisionReason};}
});
console.log(JSON.stringify({synthetic_only:true,model_called:false,tools_executed:false,rows}));
