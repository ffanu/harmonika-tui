'use strict';
// One bounded tool exposes exact registered operations without bloating model schemas.
const path=require('node:path'),fs=require('node:fs'),{spawn}=require('node:child_process');
const catalog=require('./operations_catalog.json');
const ids=new Set(catalog.skills.map(row=>row.id));
const definitions=[{name:'engine_operations',description:'Harmonika backend operations: diagnose service/RTX health, run isolated selftests, Semgrep/Trivy scan, browser regression trace, read-only database audit, license usage, existing-job resume/reconcile, and reviewed deployment/rollback. First inspect readiness with operations.engine.diagnose. Project tools require an operator-registered project alias. License scope=all requires admin. Deployment needs permission plus reviewed plan hash. Unknown job outcomes must never be resubmitted.',inputSchema:{type:'object',properties:{skill_id:{type:'string',enum:[...ids].filter(id=>id!=='operations.artifact.read')},input:{type:'object',description:'Exact operation input. project is a registered alias, never a local/server path.'}},required:['skill_id','input'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true}}];
function invoke(skill_id,input,{python=process.env.HARMONIKA_PYTHON||'python3',execute=spawn}={}){
 if(!ids.has(skill_id)||!input||typeof input!=='object'||Array.isArray(input))return Promise.resolve({ok:false,error:{code:'invalid_operations_request'}});
 const body=JSON.stringify(input);if(Buffer.byteLength(body)>1048576)return Promise.resolve({ok:false,error:{code:'input_too_large'}});
 return new Promise(resolve=>{
  let done=false,out=[],bytes=0,child;
  const finish=value=>{if(done)return;done=true;clearTimeout(timer);resolve(value)};
  const failed=()=>({ok:false,error:{code:'operations_client_failed'},retry_safe:false,operation_state:'unknown'});
  const timer=setTimeout(()=>{if(child)child.kill();finish(failed())},55000);timer.unref?.();
  try{child=execute(python,['-I',path.join(__dirname,'operations_cli.py'),'invoke',skill_id],{stdio:['pipe','pipe','ignore'],windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUTF8:'1'}})}catch{finish(failed());return}
  child.on('error',()=>finish(failed()));child.stdin.on('error',()=>{});
  child.stdout.on('data',b=>{bytes+=b.length;if(bytes>262144){child.kill();finish(failed())}else out.push(b)});
  child.on('close',()=>{if(done)return;try{const value=JSON.parse(Buffer.concat(out).toString('utf8'));finish(value&&typeof value==='object'&&!Array.isArray(value)?value:failed())}catch{finish(failed())}});
  child.stdin.end(body);
 });
}
module.exports={definitions,invoke};
