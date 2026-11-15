const {test}=require('node:test');
const assert=require('node:assert/strict');
const {decideRetry,mechanics,retryAdvice}=require('../runtime/tui-control/retry.mjs');
const {createMonitor}=require('../runtime/tui-control/model.mjs');
const cause=(code,status=503)=>({type:'provider.internal',status,response:{body:JSON.stringify({error:{code,message:'PRIVATE_SENTINEL'}})},get message(){throw Error('private error message accessed')}});
test('wrapped policy and schema failures stop immediately even behind HTTP 503',()=>{
 for(const code of ['tool_secret_reference_required','invalid_tools','invalid_tool_arguments','invalid_tool_response','invalid_tool_history'])
  assert.equal(decideRetry(cause(code),2,{retry:true,delay:1}).retry,false,code);
});
test('HTTP authentication, validation and quota never retry or switch providers',()=>{
 for(const status of [400,401,402,403,404,409,413,415,422])assert.equal(decideRetry({status,type:'provider.internal'},2,{retry:true}).retry,false);
 for(const code of ['quota_exceeded','insufficient_quota','entitlement_denied'])assert.equal(decideRetry(cause(code,429),2,{retry:true}).retry,false);
});
test('temporary failures allow only two retries and timeouts only one',()=>{
 for(const code of ['tool_engine_unavailable','tool_engine_busy','backend_unavailable','rate_limit_exceeded']){
  assert.equal(decideRetry(cause(code),2,{retry:true}).retry,true);
  assert.equal(decideRetry(cause(code),3,{retry:true}).retry,true);
  assert.equal(decideRetry(cause(code),4,{retry:true}).retry,false);
 }
 assert.equal(decideRetry(cause('tool_engine_timeout'),2,{retry:true}).retry,true);
 assert.equal(decideRetry(cause('tool_engine_timeout'),3,{retry:true}).retry,false);
});
test('never enable a native denial, retry an unknown failure, or replay partial work',()=>{
 assert.equal(decideRetry(cause('tool_engine_busy'),2,{retry:false}).retry,false);
 assert.equal(decideRetry({type:'unknown'},2,{retry:true}).retry,false);
 assert.equal(decideRetry(cause('tool_engine_busy'),2,{retry:true},true).retry,false);
 for(const attempt of [undefined,-1,0,1,NaN,1000])assert.equal(decideRetry(cause('tool_engine_busy'),attempt,{retry:true}).retry,false);
});
test('parse only bounded selected error codes, never error messages or response text',()=>{
 assert.equal(mechanics(cause('untrusted-customer-name')).code,null);
 assert.equal(mechanics({status:503,response:{body:'x'.repeat(16385)}}).code,null);
 assert.equal(mechanics({status:503,response:{body:'{malformed'}}).code,null);
 const m=createMonitor();m.recordRetry('ses_1',decideRetry(cause('tool_secret_reference_required'),2,{retry:true}));
 assert.ok(!JSON.stringify(m.get('ses_1')).includes('PRIVATE_SENTINEL'));
 assert.match(retryAdvice(m.get('ses_1').retry.reason),/referensi credential/);
});
test('advice has fixed text and never incorporates supplied content',()=>{
 assert.equal(retryAdvice('PRIVATE_SENTINEL'),'Periksa status pekerjaan.');
});
