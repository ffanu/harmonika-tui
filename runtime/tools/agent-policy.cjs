'use strict';
// Static diagnostics only. This module never grants access or executes an action.
const raw=require('./agent-policy.json');
const freeze=v=>{if(v&&typeof v==='object'){for(const x of Object.values(v))freeze(x);Object.freeze(v)}return v};
const contract=freeze(raw),codes=new Map();
for(const [category,spec] of Object.entries(contract.blockers))for(const code of spec.codes){if(codes.has(code))throw Error('duplicate_policy_code');codes.set(code,{category,spec})}
function describeBlocker(code,context={}){
 if(typeof code!=='string')return null;
 const matched=codes.get(code);if(!matched)return null;
 const {category,spec}=matched;
 const uncertain=context.uncertain===true;
 return {version:contract.version,category,reason_code:code,message:spec.message,
  next_step:uncertain?'Periksa atau rekonsiliasi operasi dengan identitas job yang sama. Hasil belum pasti; jangan mengulangi mutasi.':spec.next_step,
  may_continue_read_only:spec.may_continue_read_only,grants_permission:false,retry_mutation:false,
  recovery:uncertain?'observe_existing_job':spec.recovery};
}
function enrichResult(value){
 if(!value||typeof value!=='object'||Array.isArray(value)||value.ok===true||value.success===true&&value.ok!==false)return value;
 const code=typeof value.error==='string'?value.error:value.error&&typeof value.error.code==='string'?value.error.code:null;
 const uncertain=['unknown','uncertain','pending','running','reconciling','cancel_requested'].includes(value.operation_state)||['unknown','pending','running','reconciling','cancel_requested'].includes(value.status);
 const diagnostic=describeBlocker(code,{uncertain});return diagnostic?{...value,diagnostic}:value;
}
module.exports={version:contract.version,contract,describeBlocker,enrichResult,instructions:()=>contract.instructions,hotInstructions:()=>contract.hot_workflow};
