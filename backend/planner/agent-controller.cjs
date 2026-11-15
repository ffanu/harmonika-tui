'use strict';
// Per-request state derived from caller-owned history. No database or tool executor.
const {createHash}=require('node:crypto');
const debugWorkflow=require('./debug-workflow.cjs');
const jobLifecycle=require('./job-lifecycle.cjs');
const PHASES=['answer','discover','execute','verify','complete','waiting','blocked'];
const hash=x=>createHash('sha256').update(x).digest('hex');
function canonical(value){
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
  return value;
}
function signature(name,args){return hash(JSON.stringify([name,canonical(typeof args==='string'?JSON.parse(args):args)]));}
function text(content){return typeof content==='string'?content:Array.isArray(content)?content.filter(p=>p?.type==='text').map(p=>p.text||'').join('\n'):'';}
function followup(value){
  const input=value.replace(/<[^>]+>/g,'').trim();
  return /^(?:(?:ya|ok|oke)[,\s]+)?(?:(?:silakan|silahkan|tolong|please)\s+)?(?:lanjut(?:kan)?(?:\s+terus)?(?:\s+(?:pekerjaan|tugas|kerjaan)(?:nya|\s+(?:ini|itu|tadi|yang\s+(?:sama|tadi|belum\s+selesai)))?)?|terus(?:kan)?(?:\s+(?:pekerjaan|tugas)(?:nya|\s+(?:ini|itu|yang\s+sama))?)?|continue(?:\s+the\s+same\s+(?:task|work))?|proceed|go\s+(?:on|ahead)|keep\s+going|ok|oke|ya|retry|coba\s+lagi)[.!?\s]*$/i.test(input);
}
function failed(content){
  let value=content;try{if(typeof content==='string')value=JSON.parse(content);}catch{return false;}
  if(!value||typeof value!=='object')return false;
  const exit=value=>typeof value==='number'&&Number.isInteger(value)?value:typeof value==='string'&&/^-?\d{1,3}$/.test(value)?Number(value):null;
  const signal=(v,nested=false)=>!!v&&typeof v==='object'&&(v.ok===false||v.success===false||v.isError===true
    ||typeof v.status==='string'&&['failed','error'].includes(v.status.toLowerCase())
    ||(nested?['exit_code','exit_status','exitCode','exit']:['exit_code','exit_status','exitCode']).some(k=>{const n=exit(v[k]);return n!==null&&n>=-255&&n<=255&&n!==0}));
  if(signal(value))return true;
  // Read only explicit structured executor metadata, never error-like words in source/log text.
  if(Array.isArray(value.content)||typeof value.output==='string'){
    return signal(value.metadata,true)||signal(value.metadata?.metadata,true);
  }
  return false;
}
function observe(data){
  const messages=data.messages||[];let start=0;
  for(let i=messages.length-1;i>=0;i--)if(messages[i].role==='user'&&!followup(text(messages[i].content))){start=i;break;}
  const calls=new Map(),results=[];
  for(const message of messages.slice(start)){
    if(message.role==='assistant')for(const call of message.tool_calls||[]){
      try{calls.set(call.id,{id:call.id,signature:signature(call.function.name,call.function.arguments)});}catch{}
    }
    if(message.role==='tool'&&calls.has(message.tool_call_id)){
      const body=JSON.stringify(message.content??'');
      const lifecycle=jobLifecycle.classify(message.content);
      results.push({...calls.get(message.tool_call_id),result:hash(body),empty:!text(message.content).trim(),failed:failed(message.content)||lifecycle.failed,pending:lifecycle.pending});
    }
  }
  const last=results.at(-1);let repeats=0;
  if(last)for(let i=results.length-1;i>=0;i--){
    if(results[i].signature!==last.signature||results[i].result!==last.result)break;
    repeats++;
  }
  const emptyChecks=new Map(),emptyTotals=new Map();
  for(const result of results){
    if(!result.empty){emptyChecks.clear();emptyTotals.delete(result.signature);continue;}
    emptyChecks.set(result.signature,(emptyChecks.get(result.signature)||0)+1);
    emptyTotals.set(result.signature,(emptyTotals.get(result.signature)||0)+1);
  }
  return {results,repeats,last,emptyChecks,emptyTotals,ids:new Set(results.map(r=>r.id))};
}
function extendSchema(schema,data=null){
  const result=JSON.parse(JSON.stringify(schema));
  result.required=[...new Set([...result.required,'task_state'])];
  result.properties.task_state={type:'object',additionalProperties:false,
    required:['phase','remaining','evidence_call_ids','next_step'],properties:{
      phase:{type:'string',enum:PHASES},remaining:{type:'array',maxItems:8,items:{type:'string',minLength:1}},
      evidence_call_ids:{type:'array',maxItems:32,items:{type:'string'}},next_step:{type:'string'},
    }};
  if(data){
    const ids=Array.from(observe(data).ids);
    if(ids.length)result.properties.task_state.properties.evidence_call_ids.items.enum=ids;
    else result.properties.task_state.properties.evidence_call_ids.maxItems=0;
  }
  return result;
}
function reject(reason){const error=Error('invalid_tool_response');error.decisionReason=reason;throw error;}
function evidenceMap(data){
  const results=observe(data).results;
  const lastUnfinished=results.findLastIndex(r=>r.failed||r.pending);
  const eligible=r=>!r.failed&&!r.pending&&!r.empty;
  return {
    eligible_call_ids:results.filter(eligible).slice(-12).map(r=>r.id),
    fresh_call_ids:results.slice(lastUnfinished+1).filter(eligible).slice(-8).map(r=>r.id),
    ineligible_call_ids:results.filter(r=>r.failed||r.pending||r.empty).slice(-8).map(r=>r.id),
  };
}
function check(value,data){
  const s=value.task_state,observed=observe(data),calls=value.tool_calls||[];
  if(!s||!PHASES.includes(s.phase)||!Array.isArray(s.remaining)||s.remaining.length>8||s.remaining.some(x=>typeof x!=='string')
      ||!Array.isArray(s.evidence_call_ids)||s.evidence_call_ids.length>32||s.evidence_call_ids.some(x=>typeof x!=='string')
      ||typeof s.next_step!=='string'||s.next_step.length>2000)reject('task_contract');
  if(s.evidence_call_ids.some(id=>!observed.ids.has(id)))reject('unverified_completion');
  if(['discover','execute','verify'].includes(s.phase)&&!calls.length)reject('task_incomplete');
  if(s.phase==='complete'&&(calls.length||s.remaining.length||s.next_step.trim()||!s.evidence_call_ids.length||observed.last?.failed||observed.last?.pending))reject('unverified_completion');
  if(s.phase==='complete'){
    const cited=observed.results.filter(r=>s.evidence_call_ids.includes(r.id));
    const lastUnfinished=observed.results.findLastIndex(r=>r.failed||r.pending);
    const freshProof=observed.results.slice(lastUnfinished+1).some(r=>s.evidence_call_ids.includes(r.id)&&!r.empty);
    if(cited.some(r=>r.failed||r.pending)||!freshProof)reject('unverified_completion');
  }
  if(['blocked','waiting'].includes(s.phase)&&(calls.length||!s.next_step.trim()||!s.remaining.length))reject('task_incomplete');
  if(s.phase==='waiting'&&!s.evidence_call_ids.length)reject('unverified_completion');
  if(s.phase==='answer'&&(calls.length||s.remaining.length||s.next_step.trim()))reject('task_contract');
  for(const call of calls){
    const key=signature(call.name,call.arguments);
    if((observed.emptyChecks.get(key)||0)>=2||(observed.emptyTotals.get(key)||0)>=6)reject('stalled_decision');
  }
  if(observed.repeats>=2&&observed.last){
    for(const call of calls){
      if(signature(call.name,call.arguments)===observed.last.signature&&(observed.last.empty||s.phase!=='verify'))reject('stalled_decision');
    }
  }
  return {phase:s.phase,phase_source:'model_declared',remaining_count:s.remaining.length,evidence_count:s.evidence_call_ids.length,observed_result_count:observed.results.length,unchanged_result_streak:observed.repeats,max_empty_check_repeats:Math.max(0,...observed.emptyChecks.values()),max_empty_check_total:Math.max(0,...observed.emptyTotals.values())};
}
function instructions(data){
  const state=observe(data);
  return `AUTONOMOUS TASK CONTRACT v1:
Understand the requested outcome, scope and acceptance checks from the conversation. For an authorized action, use the actual declared tools, inspect their results, correct a concrete failure and verify the original outcome. Continue safe work within the existing authorization without asking the user to repeat permission. Do not stop after a promise, plan or partial implementation. Respect executor approvals and all credential/scope restrictions; autonomy does not grant new privileges.
Return task_state alongside content and tool_calls. phase=answer is for conversational answers or requested text-only output. phase=discover/execute/verify must select the next actual client tool. Keep remaining as an honest short checklist. phase=complete is allowed only after the requested work and verification are done; remaining must be empty, next_step empty, and evidence_call_ids must cite actual tool result IDs from this task. Tool result references establish observed output, not automatic proof of success: inspect exit status, failures and relevant assertions. Never declare passing tests, deployment, saved files or working login without matching evidence. Internal phase is a model declaration, not an execution receipt.
For a genuine missing prerequisite, permission or unavailable capability use phase=blocked, list unfinished work and give one concrete next_step. For an existing async job use phase=waiting with its real evidence and next step instead of spinning on unchanged output. Do not restart or duplicate uncertain actions. After two unchanged checks, inspect a different relevant status source or report a bounded waiting/blocker state. Rechecking after code/state changes is valid. User follow-ups such as lanjut continue the existing task.
Separate independent tool requests from dependent operations. A write must finish before build/test/readback. On schema or syntax errors correct the cause before retrying; never report checks as passed if they did not run. If preferred tooling is unavailable, use a declared equivalent within the authorized scope or state the exact gap. Do not invent a tool, environment, artifact, credential connector or successful result. Explain progress only when evidence changes; final prose states outcome, actual validation and material unfinished items concisely in the user's language.
Authentication source must keep runtime form/request references separate from embedded credential values. Use the declared file writer for a complete parseable source module when an authentication fragment cannot be validated; inspect the latest content first and preserve unrelated code and user edits. Do not wrap an entire program in a shell string, encode source or rename real secrets to bypass the firewall. For browser credential form input use explicit document.getElementById('password').value or document.querySelector('#password').value references, rather than an opaque custom getter call. Use configured identity/credential connectors for validation; never put fixture or real passwords in application source. A credential policy rejection is terminal for that decision: correct the source form or report the missing private reference, without repeating the same rejected request or relaxing permissions.
Alternating two empty checks does not establish progress or permit another identical empty request. After two empty results for the same signature without an intervening nonempty result, choose a different relevant status source or an honest waiting/blocker state. A changed source/state can justify a fresh verification; never read unrelated data merely to evade a retry bound. There is also a ceiling of six empty results per signature in the current task, across interleaved other receipts, until that same signature produces a nonempty result. At that ceiling use another relevant status/artifact source or report bounded waiting; do not spin, invent progress or duplicate a mutation.
When the user changes requirements within the same project, reread the current contract and working tree, preserve user edits and compatible earlier behavior, implement the new acceptance criteria and run fresh checks. Prior successful tests do not establish that the new requirements passed.
Completion evidence selection: failed baselines, lost acknowledgements and pending observations may be explained in prose, but must not be cited as successful evidence_call_ids. Use current successful outcome-specific checks plus saved artifact readback, including at least one fresh result after the last failed/pending observation. The following map is computed from this task's receipts in request memory only. Eligible means nonempty and not mechanically failed/pending; it does NOT prove readiness, correctness or relevance. Inspect the actual checks before choosing evidence. Do not rerun deployment/submission merely to obtain another receipt. The map is only for internal evidence_call_ids, never paste it into the user reply.
Receipt evidence map (mechanics only): ${JSON.stringify(evidenceMap(data))}
Observed mechanics only: completed_tool_results=${state.results.length}; unchanged_result_streak=${state.repeats}; max_empty_check_repeats=${Math.max(0,...state.emptyChecks.values())}; max_empty_check_total=${Math.max(0,...state.emptyTotals.values())}; last_result_empty=${!!state.last?.empty}. These counts do not prove task completion.
`+debugWorkflow.instructions()+jobLifecycle.instructions();
}
module.exports={observe,extendSchema,check,instructions,signature,evidenceMap};
