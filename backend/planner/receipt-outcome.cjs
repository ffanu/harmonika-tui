'use strict';
// Known executor mechanics only. No source-text scanning, persistence or authority.
const known=new Set(['accepted','submitted','started','queued','pending','running','processing','in_progress','scheduled','busy','retrying','unknown','outcome_unknown','reconciling','cancel_requested','cancelling','canceling','timeout','timed_out','completed','succeeded','success','cancelled','canceled','failed','failure','error','errored','rejected','aborted','killed','expired','queue_full','rate_limited']);
function inspect(body,{received=false,operation=false,status=false,write=false,test=false,execution=false,read=false}={}){
 const wrappedRead=read&&body&&typeof body==='object'&&(typeof body.content==='string'||typeof body.output==='string'||Array.isArray(body.content));
 const sources=(operation||status||write||test||execution||wrappedRead)?[body,body?.metadata].filter(v=>v&&typeof v==='object'&&!Array.isArray(v)):[];
 const states=sources.flatMap(v=>[v.status,v.state]).filter(v=>typeof v==='string').map(v=>v.trim().toLowerCase());
 const partial=sources.some(v=>v.partial===true||v.truncated===true||v.is_truncated===true||v.output_limited===true||v.complete===false);
 const mechanical=operation||status||write||test||execution;
 const asyncIdentity=mechanical&&sources.some(v=>['job_id','operation_id'].some(k=>typeof v[k]==='string'&&v[k].length>0&&v[k].length<=256));
 const exit=sources.flatMap(v=>[v.exit_code,v.exitCode,v.exit_status]).map(v=>typeof v==='number'&&Number.isInteger(v)?v:typeof v==='string'&&/^-?\d{1,3}$/.test(v)?Number(v):null).find(v=>v!==null);
 const unknownState=mechanical&&states.some(v=>!known.has(v));
 const missingState=(operation||status||asyncIdentity)&&states.length===0;
 const partialUncertain=partial&&(write||operation||status||((execution||test)&&!read&&!(exit!==undefined&&(test||exit===0))));
 return {partial:received&&partial,uncertain:received&&(unknownState||missingState||partialUncertain),unknown_state:received&&unknownState,missing_state:received&&missingState};
}
module.exports={inspect};
