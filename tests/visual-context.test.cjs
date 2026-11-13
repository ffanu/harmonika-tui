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
