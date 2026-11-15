'use strict';
// Synthetic, disposable workspace only. No user history, target SSH or credentials
// enter model prompts. The client executor accepts only the fixture's exact paths
// and single test command; the decision engine itself never executes tools.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const cp=require('node:child_process'),crypto=require('node:crypto');
const http=require('node:http');
const root=process.argv[2],production=process.argv.includes('--production');
const source=production?'/opt/harmonika-ai/api/internal-tool-planner.js':path.join(root,'backend/planner/internal-tool-planner.js');
const handle=production?null:require(source).handle,auth=fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim();
const tool=(name,properties,required)=>({type:'function',function:{name,parameters:{type:'object',properties,required,additionalProperties:false}}});
const tools=[tool('read_files',{files:{type:'array',items:{type:'string'},minItems:1}},['files']),tool('run_commands',{commands:{type:'array',items:{type:'string'},minItems:1}},['commands']),tool('editor',{path:{type:'string'},old:{type:'string'},new:{type:'string'}},['path','old','new'])];
const rows=[];
async function decide(messages,stream=false){
 const start=performance.now();let status,body='';
 const data={model:'harmonika-ai',messages,tools,parallel_tool_calls:false,stream};
 if(production){
  await new Promise((resolve,reject)=>{
   const req=http.request({host:'127.0.0.1',port:17890,path:'/v1/chat/completions',method:'POST',headers:{'content-type':'application/json','x-harmonika-client':'internal-agent','x-harmonika-agent-secret':auth}},res=>{status=res.statusCode;res.on('data',b=>{body+=b;});res.on('end',resolve);res.on('error',reject);});
   req.setTimeout(100000,()=>req.destroy(Error('canary_deadline')));req.on('error',reject);req.end(JSON.stringify(data));
  });
 }else await handle({headers:{'x-harmonika-agent-secret':auth},socket:{remoteAddress:'127.0.0.1'}},{writeHead(s){status=s;},write(s){body+=s;},end(s){if(s)body+=s;}},data);
 let message,error;
 if(stream&&status===200){
  const events=body.split('\n').filter(l=>l.startsWith('data: {')).map(l=>JSON.parse(l.slice(6)));
  message={role:'assistant',content:events.map(e=>e.choices[0].delta.content||'').join('')};
  const calls=events.flatMap(e=>e.choices[0].delta.tool_calls||[]);if(calls.length)message.tool_calls=calls.map(({index,...call})=>call);
  if(!body.endsWith('data: [DONE]\n\n'))throw Error('sse_incomplete');
 }else {const parsed=JSON.parse(body);message=parsed.choices?.[0]?.message;error=parsed.error?.code;}
 return {status,message,error,latency_ms:Math.round(performance.now()-start)};
}
async function workflow(){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-responsive-fixture-'));fs.chmodSync(dir,0o700);
 const fixture={'README.md':'The exported sum function must add two numbers. Only sum.js may be edited. Test with node --test sum.test.cjs. No deployments.',
 'sum.js':'exports.sum = (a, b) => a - b;\n',
 'sum.test.cjs':"const {test}=require('node:test');const assert=require('node:assert/strict');const {sum}=require('./sum');test('addition',()=>assert.equal(sum(2,3),5));\n"};
 for(const [name,content]of Object.entries(fixture))fs.writeFileSync(path.join(dir,name),content,{mode:0o600});
 const initial=cp.spawnSync(process.execPath,['--test','sum.test.cjs'],{cwd:dir,encoding:'utf8'});if(initial.status===0)throw Error('bad_fixture');
 const testHash=()=>crypto.createHash('sha256').update(fs.readFileSync(path.join(dir,'sum.test.cjs'))).digest('hex'),original=testHash();
 const messages=[{role:'user',content:'Perbaiki sum.js di workspace fixture ini sampai tes penjumlahan lulus. Mulai baca README.md dan sum.js dalam satu read_files. Hanya sum.js boleh diedit; sum.test.cjs dilindungi. Uji dengan tepat node --test sum.test.cjs. Lanjut mandiri sampai hasil diverifikasi, jangan deploy. Progres singkat berisi temuan baru. Jangan klaim berhasil sebelum hasil tes aktual.'}];
 let batched=false,testPassed=false,edits=0,completed=false;const steps=[];
 try{
  for(let turn=0;turn<7;turn++){
   const r=await decide(messages,turn===0);steps.push({turn,status:r.status,latency_ms:r.latency_ms,tool_count:r.message?.tool_calls?.length||0,error:r.error});
   if(r.status!==200)break;
   const m=r.message;messages.push(m);
   if(!m.tool_calls?.length){completed=testPassed&&edits>0;break;}
   if(m.tool_calls.length!==1)throw Error('nonparallel_violation');
   for(const call of m.tool_calls){
    const a=JSON.parse(call.function.arguments),name=call.function.name;let result;
    if(name==='read_files'){
     if(a.files.some(n=>!Object.hasOwn(fixture,n)))throw Error('fixture_path_denied');
     if(turn===0)batched=a.files.includes('README.md')&&a.files.includes('sum.js');
     result=a.files.map(n=>({path:n,content:fs.readFileSync(path.join(dir,n),'utf8')}));
    }else if(name==='editor'){
     if(a.path!=='sum.js'||!a.old||typeof a.new!=='string')throw Error('fixture_edit_denied');
     const file=path.join(dir,'sum.js'),before=fs.readFileSync(file,'utf8');if(before.split(a.old).length!==2)throw Error('fixture_anchor_ambiguous');
     fs.writeFileSync(file,before.replace(a.old,a.new),{mode:0o600});edits++;result={success:true,path:'sum.js',content:fs.readFileSync(file,'utf8')};
    }else if(name==='run_commands'){
     if(a.commands.length!==1||a.commands[0]!=='node --test sum.test.cjs')throw Error('fixture_command_denied');
     const x=cp.spawnSync(process.execPath,['--test','sum.test.cjs'],{cwd:dir,encoding:'utf8',timeout:5000});
     testPassed=x.status===0&&/# pass 1\b/.test(x.stdout)&&testHash()===original;
     result={success:true,exit_code:x.status,stdout:x.stdout,stderr:x.stderr};
    }else throw Error('fixture_tool_denied');
    messages.push({role:'tool',tool_call_id:call.id,content:JSON.stringify(result)});
   }
  }
  const check=cp.spawnSync(process.execPath,['--test','sum.test.cjs'],{cwd:dir,encoding:'utf8',timeout:5000});
  rows.push({name:'autonomous_fixture_read_edit_test',passed:completed&&batched&&testPassed&&check.status===0&&testHash()===original,batched_first_read:batched,completed,edits,independent_test_passed:check.status===0,original_tests_unchanged:testHash()===original,steps});
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
}
(async()=>{
 await workflow();
 const failure=[{role:'user',content:'Audit hasil snapshot fixture ini saja; jangan mengubah izin atau mengulang snapshot. Apakah berhasil? Jelaskan penyebab dan langkah berikutnya singkat.'},
 {role:'assistant',content:'Saya cek hasil snapshot.',tool_calls:[{id:'synthetic_snapshot',type:'function',function:{name:'run_commands',arguments:JSON.stringify({commands:['fixture snapshot']})}}]},
 {role:'tool',tool_call_id:'synthetic_snapshot',content:JSON.stringify([{query:'fixture snapshot',success:true,result:'mkdir(): Permission denied; snapshot not created',exit_code:1}])}];
 const f=await decide(failure),failureText=f.message?.content||'';
 // A negated phrase ("bukan snapshot berhasil") must not be graded as success.
 const affirmative=failureText.replace(/\b(?:bukan|tidak|belum|jangan|tanpa)\b[^.!?\n]*/gi,'');
 rows.push({name:'transport_success_is_not_snapshot_success',passed:f.status===200&&!f.message?.tool_calls&&/izin|permission/i.test(failureText)&&/gagal|belum|tidak/i.test(failureText)&&!/snapshot berhasil|sudah dibuat/i.test(affirmative),status:f.status,latency_ms:f.latency_ms,error:f.error});
 const large=[{role:'user',content:'Ini hanya uji retensi konteks sintetis. Jangan eksekusi. Jawab kode acuan terakhir saja. '+('synthetic nonprivate reference material '.repeat(34000))+' Kode acuan terakhir: LANTERN-7294.'}];
 const l=await decide(large);rows.push({name:'large_context_retained',input_chars:JSON.stringify(large).length,passed:l.status===200&&/LANTERN-7294/.test(l.message?.content||'')&&!l.message?.tool_calls,status:l.status,latency_ms:l.latency_ms,error:l.error});
 const hashes=Object.fromEntries(['internal-tool-planner.js','decision-recovery.cjs','agent-controller.cjs'].map(n=>[n,crypto.createHash('sha256').update(fs.readFileSync(path.join(path.dirname(source),n))).digest('hex')]));
 const report={synthetic_only:true,production,rows,source_sha256:hashes,passed:rows.every(r=>r.passed)};
 fs.writeFileSync(path.join(root,production?'responsive-live.json':'responsive-candidate.json'),JSON.stringify(report,null,2),{mode:0o600});
 console.log(JSON.stringify(report));if(!report.passed)process.exitCode=1;
})().catch(e=>{console.error(JSON.stringify({canary_error:e.message}));process.exitCode=1;});
