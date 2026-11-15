'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const f=require('../backend/planner/secret-firewall.cjs');

test('dynamic Bearer reference survives source transport and tool validation',()=>{
 for(const expression of ["'Bearer ' + sessions[session]",'"Bearer " + auth.token',"'Bearer '+credentialRef","'Bearer '\n+credentialRef",'`Bearer ${sessions[session]}`']){
  const content='const headers={Authorization: '+expression+'};';
  assert.equal(f.scrubText(content),content);
  assert.doesNotThrow(()=>f.assertToolRequest('write',{path:'web/client.js',content}));
 }
});
test('source in structured tool receipts stays readable without copying credential values',()=>{
 const value=JSON.stringify({content:"const headers={Authorization:'Bearer '+sessions[session]};",ok:true});
 assert.equal(f.scrubContent(value),value);
});
test('reference must be the complete value, without a literal credential suffix',()=>{
 for(const expression of ["'Bearer '+ 'synthetic-inline-value'","'Bearer '+ref+'synthetic-inline-value'","'Bearer '+ref\n+'synthetic-inline-value'","'Bearer '+ref\n.concat('synthetic-inline-value')","'Bearer '+getToken()","'Bearer '+refs['synthetic-inline-value']",'`Bearer ${"synthetic-inline-value"}`','`Bearer ${ref}synthetic-inline-value`','`Bearer ${getToken()}`']){
  const content='const headers={Authorization: '+expression+'};';
  assert.throws(()=>f.assertToolRequest('write',{path:'web/client.js',content}),/tool_secret_reference_required/);
 }
});
test('reference exception stays limited to the Authorization property and complete value',()=>{
 for(const content of ["const h={proxy_authorization:'Bearer '+ref};","?authorization='Bearer '+ref;","const h={cookie:'Bearer '+ref};"]){
  assert.throws(()=>f.assertToolRequest('write',{path:'web/client.js',content}),/tool_secret_reference_required/);
 }
 const content="const h={a:1,Authorization:'Bearer '+ref,b:2};";
 assert.equal(f.scrubText(content),content);
});
test('plain bearer tokens, sensitive objects and known key formats remain blocked',()=>{
 for(const args of [{content:'Authorization: Bearer synthetic-credential-value-12345'},
  {headers:{Authorization:'Bearer synthetic-credential-value-12345'}},
  {content:"const headers={Authorization:'Bearer '+ref, api_key:'synthetic-inline-value'};"},
  {content:"const headers={Authorization:'Bearer '+ref}; const leaked='hai_live_abcdefghijklmnopqrstuv';"}]){
  assert.throws(()=>f.assertToolRequest('write',{path:'web/client.js',...args}),/tool_secret_reference_required/);
 }
});
test('reference recognition never permits secret paths, environment dumps or redaction replay',()=>{
 for(const args of [{path:'.env',content:"Authorization:'Bearer '+ref;"},
  {command:"printenv # Authorization:'Bearer '+ref;"},
  {path:'web/client.js',content:"Authorization:'Bearer '+ref; // [REDACTED_SECRET]"}]){
  assert.throws(()=>f.assertToolRequest('shell',args),/tool_secret_reference_required/);
 }
});
