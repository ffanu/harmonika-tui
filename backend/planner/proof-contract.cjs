'use strict';
// Optional operator-issued proof contracts. Caller history is NEVER an attestation.
const fs=require('node:fs'),path=require('node:path');
const {createHash,createHmac,timingSafeEqual}=require('node:crypto');
const KINDS=['read','tests','artifact','job','liveness','deploy','migration','document','vision'];
const preparedAnchors=new WeakMap();
function bindPrepared(original,prepared){preparedAnchors.set(prepared,preparedAnchors.get(original)??anchor(original));}
const sha=x=>createHash('sha256').update(x).digest('hex');
function reject(){const e=Error('tool_proof_contract_invalid');e.decisionReason='proof_contract_invalid';throw e;}
function isFollowup(value){
  const input=value.replace(/<[^>]+>/g,'').trim();
  return /^(?:(?:ya|ok|oke)[,\s]+)?(?:(?:silakan|silahkan|tolong|please)\s+)?(?:lanjut(?:kan)?(?:\s+terus)?(?:\s+(?:pekerjaan|tugas|kerjaan)(?:nya|\s+(?:ini|itu|tadi|yang\s+(?:sama|tadi|belum\s+selesai)))?)?|terus(?:kan)?(?:\s+(?:pekerjaan|tugas)(?:nya|\s+(?:ini|itu|yang\s+sama))?)?|continue(?:\s+the\s+same\s+(?:task|work))?|proceed|go\s+(?:on|ahead)|keep\s+going|ok|oke|ya|retry|coba\s+lagi)[.!?\s]*$/i.test(input);
}
function anchor(data){
 const messages=data.messages||[];
 for(let i=messages.length-1;i>=0;i--){
  const m=messages[i];if(m.role!=='user')continue;
  const t=typeof m.content==='string'?m.content:Array.isArray(m.content)?m.content.filter(p=>p?.type==='text').map(p=>p.text||'').join('\n'):'';
  if(isFollowup(t))continue;
  return t;
 }
 return '';
}
function taskHash(text){return sha(text.replace(/\[HarmonikaProof:[a-f0-9]{32}\]/g,'').trim());}
function privateRead(file,max){
 const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
 try{const s=fs.fstatSync(fd);if(!s.isFile()||s.size>max||s.uid!==process.getuid()||(s.mode&0o077))reject();return fs.readFileSync(fd,'utf8');}finally{fs.closeSync(fd);}
}
function createReader({directory='/var/lib/harmonika-ai/proof-contracts',keyPath='/etc/harmonika-ai/evidence-issuer.key',clock=()=>Date.now()}={}){
 if(typeof fs.constants.O_NOFOLLOW!=='number'||typeof process.getuid!=='function')throw Error('proof_platform_unsupported');
 return data=>{
  const input=preparedAnchors.get(data)??anchor(data),refs=[...input.matchAll(/\[HarmonikaProof:([a-f0-9]{32})\]/g)];
  if(!refs.length)return null;
  if(refs.length!==1)reject();
  try{
   const d=fs.lstatSync(directory);if(!d.isDirectory()||d.isSymbolicLink()||d.uid!==process.getuid()||(d.mode&0o077))reject();
   const raw=JSON.parse(privateRead(path.join(directory,refs[0][1]+'.json'),65536));
   if(typeof raw.payload!=='string'||!/^([a-f0-9]{64})$/.test(raw.mac||''))reject();
   const key=privateRead(keyPath,256).trim();if(!/^[a-f0-9]{64}$/.test(key))reject();
   const mac=createHmac('sha256',Buffer.from(key,'hex')).update(raw.payload).digest();
   if(!timingSafeEqual(mac,Buffer.from(raw.mac,'hex')))reject();
   const v=JSON.parse(raw.payload),now=clock();
   if(v.version!==1||v.id!==refs[0][1]||v.task_hash!==taskHash(input)||!Number.isSafeInteger(v.issued_at)||!Number.isSafeInteger(v.expires_at)||v.issued_at>now||v.expires_at<=now||v.expires_at-v.issued_at>3600000)reject();
   for(const k of ['owner','project','target','attempt','revision'])if(!/^[a-f0-9]{32,64}$/.test(v[k]||''))reject();
   if(!Array.isArray(v.required)||!v.required.length||v.required.length>8||new Set(v.required).size!==v.required.length||v.required.some(k=>!KINDS.includes(k)))reject();
   if(!Array.isArray(v.checks)||v.checks.length>64)reject();
   return v;
  }catch{reject();}
 };
}
const load=createReader();
const positiveInt=n=>Number.isSafeInteger(n)&&n>0;
function validFacts(kind,f,revision){
 if(!f||typeof f!=='object'||Array.isArray(f))return false;
 switch(kind){
  case 'read':return positiveInt(f.bytes)&&/^[a-f0-9]{64}$/.test(f.sha256||'')&&f.complete===true;
  case 'tests':return f.exit_code===0&&Number.isSafeInteger(f.collected)&&f.collected>0&&f.passed===f.collected&&f.failed===0&&f.awaited===true&&f.source_sha256===revision;
  case 'artifact':return positiveInt(f.bytes)&&f.bytes===f.expected_bytes&&/^[a-f0-9]{64}$/.test(f.sha256||'')&&f.sha256===f.expected_sha256&&f.readback===true;
  case 'job':return f.status==='completed'&&/^[a-f0-9]{32}$/.test(f.operation_id||'')&&f.reconciled===true&&f.committed_effects===1;
  case 'liveness':return f.http_status===200&&f.reachable===true;
  case 'deploy':return f.source_sha256===revision&&Number.isSafeInteger(f.pid)&&f.pid>0&&f.listener===true&&f.functional===true&&f.tls_verified===true;
  case 'migration':return Number.isSafeInteger(f.schema_version)&&f.schema_version===f.expected_schema_version&&f.integrity==='ok'&&f.rows_preserved===true;
  case 'document':return Number.isSafeInteger(f.pages)&&f.pages>0&&f.pages_read===f.pages&&f.missing_fields===0&&f.contradictions===0;
  case 'vision':return positiveInt(f.image_bytes)&&/^[a-f0-9]{64}$/.test(f.image_sha256||'')&&f.decoded===true&&f.model_completed===true;
  default:return false;
 }
}
function assess(v,observed,now=Date.now()){
 const latest=new Map();let sequence=-1;
 for(const c of v.checks){
  // Every entry is issuer-signed; malformed or reordered entries invalidate the contract.
  if(!c||typeof c!=='object'||!KINDS.includes(c.kind)||!Number.isSafeInteger(c.sequence)||c.sequence<=sequence)reject();
  sequence=c.sequence;latest.set(c.kind,c);
 }
 const verified=[],missing=[];
 for(const kind of v.required){
  const c=latest.get(kind),r=c&&observed.results.findLast(r=>r.id===c.call_id);
  const bound=c&&['owner','project','target','attempt','revision'].every(k=>c[k]===v[k]);
  const fresh=c&&Number.isSafeInteger(c.at)&&c.at>=v.issued_at&&c.at<=now&&now-c.at<=300000;
  const origin=c&&['server_executed','independently_verified'].includes(c.provenance);
  const lastUnfinished=observed.results.findLastIndex(r=>r.failed||r.pending);
  const actual=r&&!r.failed&&!r.pending&&!r.empty&&r.result===c.result_sha256&&observed.results.indexOf(r)>lastUnfinished;
  if(bound&&fresh&&origin&&actual&&c.outcome==='passed'&&validFacts(kind,c.facts,v.revision))verified.push(kind);else missing.push(kind);
 }
 return {verified,missing,complete:missing.length===0};
}
const labels={read:'pembacaan berkas',tests:'tes pada revisi saat ini',artifact:'ukuran dan checksum artefak',job:'rekonsiliasi operasi',liveness:'konektivitas HTTP',deploy:'identitas rilis, TLS dan fungsi aplikasi',migration:'migrasi dan integritas database',document:'kelengkapan ekstraksi dokumen',vision:'pemrosesan isi gambar'};
function render(state){
 const good=state.verified.map(k=>labels[k]).join('; '),gaps=state.missing.map(k=>labels[k]).join('; ');
 return state.complete?'Pemeriksaan terverifikasi: '+good+'.':'Belum selesai. '+(good?'Sudah terverifikasi: '+good+'. ':'')+'Masih perlu diperiksa: '+gaps+'.';
}
module.exports={isFollowup,bindPrepared,load,createReader,anchor,taskHash,assess,validFacts,render,KINDS};
