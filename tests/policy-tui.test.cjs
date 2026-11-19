'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createCore,ToolError}=require('../runtime/tools/mcp-core.cjs');
const args={skill_id:'operations.engine.diagnose',input:{}};
function value(result){assert.equal(result.content.length,1);assert.equal(result.content[0].type,'text');return JSON.parse(result.content[0].text)}

test('actual MCP boundary distinguishes four exact blocker codes without granting access',async()=>{
 for(const category of ['permission_required','credential_missing','tool_unavailable','scope_denied']){
  let calls=0;
  const source={ok:false,status:'error',error:{code:category,message:'PRIVATE_ERROR_SENTINEL'},retry_safe:false};
  const core=createCore({operations:async()=>{calls++;return source}});
  const response=await core.callTool('engine_operations',args),data=value(response);
  assert.equal(response.isError,true);assert.equal(calls,1);assert.deepEqual(data.error,source.error);
  assert.equal(data.status,'error');assert.equal(data.ok,false);assert.equal(data.retry_safe,false);
  assert.equal(data.diagnostic.version,'1');assert.equal(data.diagnostic.category,category);
  assert.equal(data.diagnostic.grants_permission,false);assert.equal(data.diagnostic.retry_mutation,false);
  assert.equal(typeof data.diagnostic.may_continue_read_only,'boolean');
  assert.equal(typeof data.diagnostic.reason_code,'string');assert.equal(typeof data.diagnostic.message,'string');assert.equal(typeof data.diagnostic.next_step,'string');
  assert.doesNotMatch(JSON.stringify(data.diagnostic),/PRIVATE_ERROR_SENTINEL/);
  assert.equal(Object.hasOwn(source,'diagnostic'),false,'owned receipt must not be mutated');
 }
});

test('backend unavailable is an availability problem rather than a permission rejection',async()=>{
 const core=createCore({operations:async()=>({ok:false,error:'backend_unavailable'})});
 const response=await core.callTool('engine_operations',args),data=value(response);
 assert.equal(response.isError,true);assert.equal(data.error,'backend_unavailable');
 assert.equal(data.diagnostic.category,'tool_unavailable');
 assert.equal(data.diagnostic.recovery,'declared_equivalent_or_health_probe');
 assert.equal(data.diagnostic.grants_permission,false);
});

test('known failure replaces untrusted diagnostic prose and permission claims with the shared contract',async()=>{
 const source={ok:false,error:{code:'tool_unavailable'},diagnostic:{category:'scope_denied',message:'PRIVATE_DIAGNOSTIC_SENTINEL',grants_permission:true,retry_mutation:true}};
 const core=createCore({operations:async()=>source});
 const response=await core.callTool('engine_operations',args),data=value(response);
 assert.equal(response.isError,true);assert.equal(data.diagnostic.category,'tool_unavailable');
 assert.equal(data.diagnostic.grants_permission,false);assert.equal(data.diagnostic.retry_mutation,false);
 assert.doesNotMatch(JSON.stringify(data.diagnostic),/PRIVATE_DIAGNOSTIC_SENTINEL/);
 assert.equal(source.diagnostic.grants_permission,true,'source receipt remains unmodified');
});

test('successful user content remains successful even when its text describes a denial',async()=>{
 const content='Example document: permission_required; scope_denied; tool_unavailable; PRIVATE_DOCUMENT_SENTINEL';
 const source={ok:true,status:'completed',result:{text:content,error:{code:'scope_denied'}}};
 const core=createCore({operations:async()=>source});
 const response=await core.callTool('engine_operations',args),data=value(response);
 assert.equal(response.isError,false);assert.equal(data.ok,true);assert.deepEqual(data.result,source.result);
 assert.equal(Object.hasOwn(data,'diagnostic'),false);assert.equal(Object.hasOwn(source,'diagnostic'),false);
});

test('unknown error prose cannot fabricate a permission or credential diagnosis',async()=>{
 const source={ok:false,status:'error',error:{code:'unrecognized_failure',message:'permission_required credential_missing PRIVATE_ERROR_SENTINEL'},retry_safe:false};
 const core=createCore({operations:async()=>source});
 const response=await core.callTool('engine_operations',args),data=value(response);
 assert.equal(response.isError,true);assert.equal(Object.hasOwn(data,'diagnostic'),false);
 assert.deepEqual(data.error,source.error);assert.equal(data.retry_safe,false);
});

test('uncertain mutation keeps the same no-replay state and asks to observe the existing job',async()=>{
 let calls=0;
 const source={ok:false,status:'error',error:{code:'tool_unavailable'},operation_state:'unknown',retry_safe:false,job_ref:'fixture-job-reference'};
 const core=createCore({operations:async()=>{calls++;return source}});
 const response=await core.callTool('engine_operations',{skill_id:'operations.deploy.apply',input:{}}),data=value(response);
 assert.equal(response.isError,true);assert.equal(calls,1);assert.deepEqual(data.error,source.error);
 assert.equal(data.operation_state,'unknown');assert.equal(data.retry_safe,false);assert.equal(data.job_ref,'fixture-job-reference');
 assert.equal(data.diagnostic.category,'tool_unavailable');assert.equal(data.diagnostic.recovery,'observe_existing_job');
 assert.equal(data.diagnostic.retry_mutation,false);assert.equal(data.diagnostic.grants_permission,false);
});

test('thrown uncertain tool failures receive diagnostics without losing executor uncertainty',async()=>{
 const core=createCore({operations:async()=>{const error=new ToolError('tool_unavailable');error.uncertain=true;throw error}});
 const response=await core.callTool('engine_operations',args),data=value(response);
 assert.equal(response.isError,true);assert.equal(data.error,'tool_unavailable');assert.equal(data.operation_state,'uncertain');assert.equal(data.retry_safe,false);
 assert.equal(data.diagnostic.recovery,'observe_existing_job');assert.equal(data.diagnostic.retry_mutation,false);
});

test('existing media degradation and retry flags remain intact',async()=>{
 const core=createCore({operations:async()=>{throw new ToolError('image_engine_offline')}});
 const response=await core.callTool('engine_operations',args),data=value(response);
 assert.equal(response.isError,true);assert.equal(data.status,'degraded');assert.equal(data.operation_state,'not_dispatched');
 assert.equal(data.retryable,true);assert.equal(data.retry_safe,true);assert.equal(data.ok,false);
 if(data.diagnostic){assert.equal(data.diagnostic.retry_mutation,false);assert.equal(data.diagnostic.grants_permission,false)}
});
