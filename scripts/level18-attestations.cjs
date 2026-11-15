'use strict';
// Audit historical synthetic attestations without exporting their signing key,
// source, transcripts or unrelated operator records.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const stage=process.argv[2],report=JSON.parse(fs.readFileSync(path.join(stage,'level18-candidate.json'),'utf8'));
if(!report.passed||report.rows.length!==12)throw Error('native_exam_not_complete');
const key=Buffer.from(fs.readFileSync('/etc/harmonika-ai/evidence-issuer.key','utf8').trim(),'hex');
const sha=x=>crypto.createHash('sha256').update(x).digest('hex');
const ledger='/var/lib/harmonika-ai/proof-contracts',proofFiles=fs.readdirSync(ledger).filter(n=>/^[a-f0-9]{32}\.json$/.test(n));
const workspaces=fs.readdirSync(osTmp()).filter(n=>/^harmonika-l18-[PU]\d\d-/.test(n)).map(n=>path.join(osTmp(),n));
function osTmp(){return require('node:os').tmpdir();}
const attestations=[];
for(const row of report.rows){
 const finished=fs.statSync(path.join(stage,'mission-'+row.id+'.json')).mtimeMs,start=finished-row.duration_ms;
 const candidates=workspaces.filter(n=>path.basename(n).startsWith('harmonika-l18-'+row.id+'-'));
 const targets=new Map(candidates.map(n=>[sha(n).slice(0,32),n]));let found=null;
 for(const file of proofFiles){
  const stat=fs.statSync(path.join(ledger,file));if(stat.mtimeMs<start-3000||stat.mtimeMs>finished+3000)continue;
  let envelope,value;try{envelope=JSON.parse(fs.readFileSync(path.join(ledger,file),'utf8'));value=JSON.parse(envelope.payload);}catch{continue;}
  if(value.issued_at<start-3000||value.issued_at>start+1000||!targets.has(value.target))continue;
  const expected=crypto.createHmac('sha256',key).update(envelope.payload).digest('hex');if(expected!==envelope.mac)throw Error('historical_signature_invalid');
  if(found)throw Error('ambiguous_fixture_attestation');found={value,dir:targets.get(value.target)};
 }
 if(!found)throw Error('fixture_attestation_missing_'+row.id);
 const {value:v,dir}=found,job=['lost_ack','busy_job','foreign_job'].includes(row.case),cancel=row.case==='cancel_ack',artifact=row.case==='artifact_mismatch';
 const entry=row.case==='nested_config'?'config/runtime/active.cjs':'src/main.cjs';
 const writes=job||cancel?[]:artifact?['report.txt']:row.case==='chained_faults'?[entry,'src/line.cjs']:[entry];
 const protectedNames=job||cancel||artifact?['README.md']:row.case==='nested_config'?['archive/example.cjs','test/contract.test.cjs','README.md']:['test/contract.test.cjs','README.md'];
 const protectedHashes=protectedNames.map(n=>[n,sha(fs.readFileSync(path.join(dir,n)))]);
 const revision=sha(JSON.stringify(writes.sort().map(n=>[n,sha(fs.readFileSync(path.join(dir,n)))]).concat(protectedHashes)));
 if(revision!==v.revision)throw Error('workspace_revision_changed_'+row.id);
 const latest=new Map();for(const check of v.checks)latest.set(check.kind,check);
 const checks=v.required.map(kind=>{
  const c=latest.get(kind);if(!c||c.outcome!=='passed'||c.revision!==v.revision||!['owner','project','target','attempt'].every(k=>c[k]===v[k]))throw Error('attestation_binding_invalid');
  return {kind:c.kind,sequence:c.sequence,provenance:c.provenance,at:c.at,result_sha256:c.result_sha256,facts:c.facts};
 });
 attestations.push({id:row.id,contract_id:v.id,task_hash:v.task_hash,owner:v.owner,project:v.project,target:v.target,attempt:v.attempt,revision:v.revision,historical_signature_valid:true,current_workspace_revision_matches:true,protected_file_sha256:Object.fromEntries(protectedHashes),required:v.required,allowed_tools:v.allowed_tools,checks});
}
const out={synthetic_only:true,historical_audit_not_new_authorization:true,signing_key_exported:false,all_verified:attestations.length===12,attestations};
fs.writeFileSync(path.join(stage,'level18-attestations.json'),JSON.stringify(out,null,2),{mode:0o600});console.log(JSON.stringify({attestations:attestations.length,all_verified:out.all_verified,signing_key_exported:false}));
