const test=require('node:test'),assert=require('node:assert/strict');
const {createCore,publicMetadata}=require('../runtime/tools/mcp-core.cjs');
test('all engine metadata uses one public brand, without modifying source objects or document text',()=>{
 const original={provider:'rtx',model:'codex',engine:'opencode',nested:{provider_name:'google',fallback_provider:'claude',providers:['rtx','codex']},text:'User document mentions Codex and Google; keep it accurate.',ui_elements:[{model:'EG8145V5',provider:'example from customer document'}],error:'codex_timeout'};
 const result=publicMetadata(original);
 assert.equal(result.provider,'Harmonika Ai');assert.equal(result.model,'Harmonika Ai');assert.equal(result.engine,'Harmonika Ai');
 assert.equal(result.nested.provider_name,'Harmonika Ai');assert.equal(result.nested.fallback_provider,'Harmonika Ai');assert.deepEqual(result.nested.providers,['Harmonika Ai']);
 assert.equal(result.text,original.text);assert.equal(original.provider,'rtx');assert.equal(result.error,'harmonika_timeout');
 assert.deepEqual(result.ui_elements,original.ui_elements);
});
test('tool success, degraded errors and truncated output retain consistent branding',async()=>{
 for(const response of [{ok:true,status:'ok',provider:'google',answer:'answer'}, {ok:false,status:'degraded',error:'codex_unavailable'}, {ok:true,status:'ok',provider:'rtx',answer:'x'.repeat(60000)}]) {
  const c=createCore({rpc:async()=>response});const r=await c.callTool('web_search',{query:'test'}),data=JSON.parse(r.content[0].text);
  assert.equal(data.provider,'Harmonika Ai');assert.doesNotMatch(r.content[0].text,/"provider":"(?:rtx|codex|google)"/);
  if(!response.ok){assert.equal(r.isError,true);assert.equal(data.error,'harmonika_unavailable');}
 }
});
