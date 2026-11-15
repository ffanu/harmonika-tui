'use strict';
// Pure synthetic validity probes; no model, executor or mutable external state.
const path=require('node:path'),controller=require(path.join(process.argv[2]||path.join(__dirname,'../backend/planner'),'agent-controller.cjs'));
const cases=[
 ['failed_test_report','Jalankan tes proyek ini dan laporkan hasilnya. Jangan ubah file apa pun.','npm test',false,'# tests 2\n# pass 1\n# fail 1\n','Ada satu tes gagal.'],
 ['zero_tests_report','Jalankan tes proyek ini dan laporkan jumlahnya. Jangan ubah file apa pun.','npm test',true,'# tests 0\n# pass 0\n# fail 0\n','Jumlah tes nol; tidak ada tes ditemukan.'],
 ['syntax_report','Periksa sintaks proyek ini dan laporkan error. Jangan ubah file apa pun.','npm run check',false,'SyntaxError: Unexpected end of input','Pemeriksaan sintaks mengembalikan SyntaxError. File tidak diubah.']
];
const rows=cases.map(([id,prompt,command,success,result,content])=>{
 const data={tools:['read_files','run_commands','editor'].map(name=>({type:'function',function:{name,parameters:{type:'object',properties:{}}}})),messages:[
  {role:'user',content:prompt},{role:'assistant',tool_calls:[{id:'check',function:{name:'run_commands',arguments:JSON.stringify({commands:[command]})}}]},
  {role:'tool',tool_call_id:'check',content:JSON.stringify([{query:command,success,result}])}]};
 const value={content,tool_calls:[],task_state:{phase:'answer',remaining:[],next_step:'',evidence_call_ids:['check']}};
 try{controller.check(value,data);return {id,accepted:true,reason:null};}catch(e){return {id,accepted:false,reason:e.decisionReason};}
});
console.log(JSON.stringify({synthetic_only:true,model_called:false,tools_executed:false,rows}));
