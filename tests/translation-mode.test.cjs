"use strict";
const test=require('node:test'),assert=require('node:assert/strict');
const mode=require('../backend/planner/translation-mode.cjs');
const input=()=>({harmonika_translation:true,stream:false,tools:[],tool_choice:'none',messages:[{role:'system',content:'Fixed policy.'},{role:'user',content:'Quoted task data.'}]});
test('untrusted flag and serialized clones do not authorize faithful mode',()=>{
 const d=input();assert.equal(mode.isTrusted(d),false);mode.authorize(d);assert.equal(mode.isTrusted(d),true);
 const clone=JSON.parse(JSON.stringify(d));assert.equal(mode.isTrusted(clone),false);mode.inherit(d,clone);assert.equal(mode.isTrusted(clone),true);
});
test('ordinary requests are untouched',()=>{const d={messages:[]};assert.equal(mode.authorize(d),false);assert.equal(mode.isTrusted(d),false);});
test('translation refuses tools streaming nontext and oversized data',()=>{
 for(const mutate of [x=>x.tools=[{}],x=>x.tool_choice='auto',x=>x.stream=true,x=>x.messages[0].role='assistant',x=>x.messages[1].content=[{type:'text',text:'x'}],x=>x.messages[1].content='x'.repeat(262144)]){
  const d=input();mutate(d);assert.throws(()=>mode.authorize(d),/translation_invalid_request/);assert.equal(mode.isTrusted(d),false);
 }
});
