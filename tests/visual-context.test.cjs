const {test}=require('node:test');const assert=require('node:assert/strict');
const {project,BUDGET}=require('../backend/visual_quality/context_window.cjs');
const call=id=>({role:'assistant',content:null,tool_calls:[{id,type:'function',function:{name:'execute',arguments:'{"path":"website/index.html"}'}}]});
const result=id=>({role:'tool',tool_call_id:id,content:'large synthetic output '.repeat(9000)});
test('long visual sessions preserve latest request, schemas, paired tools and source session',()=>{
 const input={tools:[{name:'execute',schema:{type:'object'}}],messages:[{role:'system',content:'rules '.repeat(5000)},{role:'user',content:'build website, keep prices'},call('a'),result('a'),{role:'assistant',content:'website exists'},{role:'user',content:'build poster'},call('b'),result('b'),{role:'user',content:'Revise website CTA only, keep all prices'},call('c'),{role:'tool',tool_call_id:'c',content:'exact current receipt'}]};
 const original=JSON.stringify(input);const out=project(input);
 assert.equal(JSON.stringify(input),original);assert.ok(JSON.stringify(out).length<=BUDGET);assert.deepEqual(out.tools,input.tools);
 assert.ok(out.messages.some(m=>m.content==='Revise website CTA only, keep all prices'));
 assert.ok(out.messages.some(m=>m.content==='build website, keep prices'));
 assert.equal(out.messages.at(-1).content,'exact current receipt');
 for(const m of out.messages.filter(m=>m.role==='tool'))assert.ok(out.messages.some(a=>a.tool_calls?.some(c=>c.id===m.tool_call_id)));
});
test('mandatory oversized request is never silently clipped',()=>{
 const input={messages:[{role:'user',content:'u'.repeat(BUDGET+10)}]};assert.equal(project(input),input);
});
test('small sessions are passed through unchanged',()=>{const input={messages:[{role:'user',content:'read'}]};assert.equal(project(input),input)});
test('historical binary screenshots cannot inflate a later revision request',()=>{
 const input={messages:[{role:'system',content:'rules'},{role:'user',content:[{type:'text',text:'Website request; preserve prices'},{type:'image_url',image_url:{url:'data:image/png;base64,'+'a'.repeat(1600000)}}]},{role:'assistant',content:'website created'},{role:'user',content:'Change only website CTA'}]};
 const out=project(input);assert.ok(JSON.stringify(out).length<BUDGET);
 assert.equal(out.messages.at(-1).content,'Change only website CTA');assert.ok(JSON.stringify(out).includes('Website request; preserve prices'));
 assert.ok(JSON.stringify(input).length>1600000);assert.ok(!JSON.stringify(out).includes('base64'));
});
test('oversized current media keeps safe history reductions instead of restoring old screenshots',()=>{
 const oldImage='data:image/png;base64,'+'a'.repeat(200000);
 const currentImage='data:image/png;base64,'+'b'.repeat(BUDGET+1000);
 const input={tools:[{name:'inspect'}],messages:[
  {role:'system',content:'retain system instructions'},
  {role:'user',content:[{type:'text',text:'Keep the original design brief'},{type:'image_url',image_url:{url:oldImage}}]},
  call('old'),result('old'),
  {role:'user',content:[{type:'text',text:'Inspect this actual current image'},{type:'image_url',image_url:{url:currentImage}}]},
  call('current'),{role:'tool',tool_call_id:'current',content:'exact current receipt'}
 ]};
 const original=JSON.stringify(input);const out=project(input);
 assert.ok(JSON.stringify(out).length>BUDGET);
 assert.ok(JSON.stringify(out).length<original.length/2);
 assert.equal(JSON.stringify(input),original);
 assert.deepEqual(out.tools,input.tools);
 assert.equal(out.messages[0].content,'retain system instructions');
 assert.ok(JSON.stringify(out).includes('Keep the original design brief'));
 assert.ok(!JSON.stringify(out).includes(oldImage));
 assert.ok(JSON.stringify(out).includes(currentImage));
 assert.equal(out.messages.at(-1).content,'exact current receipt');
 for(const m of out.messages.filter(m=>m.role==='tool'))assert.ok(out.messages.some(a=>a.tool_calls?.some(c=>c.id===m.tool_call_id)));
});
test('tool image attachments do not move the task boundary past current QA receipts',()=>{
 const receipt='{"coverage_complete":true,"tile_count":3,"source_height":5400}';
 const input={messages:[{role:'system',content:'rules'},
  {role:'user',content:'Old task'},call('old'),result('old'),
  {role:'user',content:'Read QA and overview, then finish without new jobs'},
  call('qa'),{role:'tool',tool_call_id:'qa',content:receipt},
  {role:'assistant',content:'Current independent assessment; preserve this evidence'},
  call('image'),{role:'tool',tool_call_id:'image',content:'Read image file'},
  {role:'user',content:[{type:'image_url',image_url:{url:'data:image/png;base64,'+'b'.repeat(155000)}}]}
 ]};
 const out=project(input);
 assert.ok(out.messages.some(m=>m.role==='tool'&&m.tool_call_id==='qa'&&m.content===receipt));
 assert.ok(out.messages.some(m=>m.content==='Current independent assessment; preserve this evidence'));
 assert.deepEqual(out.messages.at(-1),input.messages.at(-1));
 assert.ok(!out.messages.some(m=>m.role==='tool'&&m.tool_call_id==='old'));
 for(const m of out.messages.filter(m=>m.role==='tool'))assert.ok(out.messages.some(a=>a.tool_calls?.some(c=>c.id===m.tool_call_id)));
});
