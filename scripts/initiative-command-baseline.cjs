'use strict';
// Five synthetic legacy checks. No model call, executor, disk writes or credentials.
const path=require('node:path');
const a=require(path.join(process.argv[2]||path.join(__dirname,'../backend/planner'),'agent-controller.cjs'));
const tools=[{type:'function',function:{name:'read_files',parameters:{type:'object',properties:{}}}}];
const rows=[];
for(const p of ['Cek kenapa total pesanan di proyek ini salah, lalu bereskan.','Audit endpoint aplikasi ini.','Telusuri penyebab tes proyek ini gagal.','Temukan bug pada proyek ini.']){
 const d={tools,messages:[{role:'user',content:p}]};
 const v={content:'Saya akan memeriksa.',tool_calls:[],task_state:{phase:'answer',remaining:[],evidence_call_ids:[],next_step:''}};
 let accepted=true;try{a.check(v,d);}catch{accepted=false;}
 rows.push({mode:a.requestMode(d),accepted});
}
const d={messages:[{role:'user',content:'Perbaiki bug aplikasi ini.'},
 {role:'assistant',tool_calls:[{id:'x',function:{name:'run_commands',arguments:'{"commands":["pwd"]}'}}]},
 {role:'tool',tool_call_id:'x',content:JSON.stringify([{query:'pwd',success:true,exit_code:0,result:'/synthetic/project'}])}]};
let accepted=true;
try{a.check({content:'Tes lulus.',tool_calls:[],task_state:{phase:'complete',remaining:[],evidence_call_ids:['x'],next_step:''}},d);}catch{accepted=false;}
rows.push({category:'test_claim_from_pwd',accepted});
console.log(JSON.stringify({synthetic_only:true,model_called:false,tools_executed:false,rows}));
