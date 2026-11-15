'use strict';
// Independent bounded verifier for owned synthetic fixtures; no source execution on host.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const root=path.resolve(process.argv[2]||'');
if(!path.basename(root).startsWith('harmonika-initiative-exam-'))throw Error('fixture_root_required');
const inputs=[[[{price:3,qty:4},{price:10,qty:0}]],[[{price:2,qty:7}]],[[]],[[{price:5,qty:1},{price:6,qty:2}]]];
const expected=[12,14,0,17],rows=[];
for(const name of ['arithmetic','chained','tool_fallback','discovery_decoy']){
 const dir=path.join(root,'native-'+name,'initiative-fixture');
 const sources={},cache={};
 for(const n of ['src/invoice.cjs','src/line.cjs','modules/orders.cjs']){
  const p=path.join(dir,n);if(fs.existsSync(p))sources[n]=fs.readFileSync(p,'utf8');
 }
 function load(n){
  if(cache[n])return cache[n].exports;
  if(!Object.hasOwn(sources,n))throw Error('fixture_dependency_denied');
  const m={exports:{}};cache[n]=m;
  vm.runInNewContext(sources[n],{exports:m.exports,module:m,require:r=>load(path.posix.normalize(path.posix.join(path.posix.dirname(n),r)))},{timeout:1000});
  return m.exports;
 }
 let passed=false;
 try{
  const fn=load(name==='discovery_decoy'?'modules/orders.cjs':'src/invoice.cjs').total;
  // Execute every held-out call under a deadline in the same bounded context.
  passed=inputs.every((args,i)=>vm.runInNewContext('fn(...args)',{fn,args:JSON.parse(JSON.stringify(args))},{timeout:1000})===expected[i]);
 }catch{}
 rows.push({case:name,passed,checks:inputs.length,source_sha256:Object.fromEntries(Object.entries(sources).map(([n,s])=>[n,crypto.createHash('sha256').update(s).digest('hex')]))});
}
const report={synthetic_only:true,independent_from_visible_test_process:true,bounded_not_a_hostile_code_sandbox:true,passed:rows.every(r=>r.passed),rows};
fs.writeFileSync(path.join(root,'heldout-verification.json'),JSON.stringify(report,null,2),{mode:0o600});
console.log(JSON.stringify(report));if(!report.passed)process.exitCode=1;
