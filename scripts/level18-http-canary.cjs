'use strict';
// Actual authenticated loopback HTTP; invalid-scope probes never execute a tool.
const fs=require('node:fs'),crypto=require('node:crypto'),http=require('node:http'),path=require('node:path');
const root=process.argv[2],proof=require('/opt/harmonika-ai/api/proof-contract.cjs');
const auth=fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim();
const key=Buffer.from(fs.readFileSync('/etc/harmonika-ai/evidence-issuer.key','utf8').trim(),'hex');
const sha=x=>crypto.createHash('sha256').update(x).digest('hex'),id=crypto.randomBytes(16).toString('hex'),now=Date.now();
const prompt='Verifikasi scope fixture. [HarmonikaProof:'+id+']';
const contract={version:1,id,task_hash:proof.taskHash(prompt),issued_at:now,expires_at:now+300000,owner:sha('fixture-owner').slice(0,32),project:sha('fixture-project').slice(0,32),target:sha('fixture-target').slice(0,32),attempt:sha(id).slice(0,32),revision:sha('fixture-revision'),required:['read'],checks:[],allowed_tools:['fixture.read']};
const payload=JSON.stringify(contract);fs.writeFileSync('/var/lib/harmonika-ai/proof-contracts/'+id+'.json',JSON.stringify({payload,mac:crypto.createHmac('sha256',key).update(payload).digest('hex')}),{mode:0o600,flag:'wx'});
const tools=['fixture.read','fixture.edit'].map(name=>({type:'function',function:{name,parameters:{type:'object',properties:{path:{type:'string'}},required:['path'],additionalProperties:false}}}));
function call(data,authorized=true){return new Promise((resolve,reject)=>{
 const headers={'content-type':'application/json','x-harmonika-client':'internal-agent'};if(authorized)headers['x-harmonika-agent-secret']=auth;
 const req=http.request({host:'127.0.0.1',port:17890,path:'/v1/chat/completions',method:'POST',headers},res=>{let raw='';res.on('data',b=>{raw+=b;});res.on('end',()=>{try{resolve({status:res.statusCode,json:JSON.parse(raw)});}catch{reject(Error('canary_invalid_json'));}});});req.on('error',reject);req.setTimeout(100000,()=>req.destroy(Error('canary_deadline')));req.end(JSON.stringify(data));
});}
(async()=>{
 const data={model:'harmonika-ai',stream:false,messages:[{role:'user',content:prompt}],tools,tool_choice:{type:'function',function:{name:'fixture.edit'}}};
 const statuses=[];
 for(let i=0;i<12;i++){const r=await call(data);statuses.push({status:r.status,code:r.json.error?.code});}
 const healthy=await call({model:'harmonika-ai',stream:false,messages:[{role:'user',content:'Apa fungsi DNS? Jawab satu kalimat, tanpa tindakan.'}],tools:[],tool_choice:'none'});
 const incomplete=await call({model:'harmonika-ai',stream:false,messages:[{role:'user',content:'Baca fixture sintetis.'},{role:'assistant',content:null,tool_calls:[{id:'fixture-missing-result',type:'function',function:{name:'fixture.read',arguments:'{"path":"README.md"}'}}]}],tools,tool_choice:'auto'});
 const denied=await call({model:'harmonika-ai',stream:false,messages:[{role:'user',content:'Synthetic fixture.'}],tools:[]},false);
 const report={actual_loopback_http:true,tools_executed:false,signed_scope:true,denied_scope:statuses,subsequent_valid_status:healthy.status,subsequent_valid_text:!!healthy.json.choices?.[0]?.message?.content,missing_result_status:incomplete.status,missing_result_code:incomplete.json.error?.code,unauthenticated_status:denied.status};
 report.passed=statuses.every(r=>r.status===403&&r.code==='tool_scope_denied')&&healthy.status===200&&report.subsequent_valid_text&&incomplete.status===400&&report.missing_result_code==='invalid_tool_history'&&denied.status===403;
 fs.writeFileSync(path.join(root,'level18-http-canary.json'),JSON.stringify(report,null,2),{mode:0o600});console.log(JSON.stringify(report));if(!report.passed)process.exitCode=1;
})().catch(e=>{console.error(JSON.stringify({canary_failed:e.message}));process.exitCode=1;});
