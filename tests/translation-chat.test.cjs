'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');
const {create,parseIntent}=require('../backend/planner/translation-chat.cjs');
const data=content=>({messages:[{role:'user',content}]});
function response(){return {headers:{},body:'',setHeader(k,v){this.headers[k]=v;},writeHead(s,h){this.status=s;Object.assign(this.headers,h);},write(s){this.body+=s;},end(s=''){this.body+=s;}};}
test('explicit translation grammar preserves text and registers',()=>{
 assert.deepEqual(parseIntent(data('Terjemahkan ke bahasa Jawa krama: Selamat pagi {name}.')),{source:'auto',target:'Jawa krama',text:'Selamat pagi {name}.'});
 assert.deepEqual(parseIntent(data('Terjemahkan dari bahasa Inggris ke bahasa Indonesia: Good morning')),{source:'Inggris',target:'Indonesia',text:'Good morning'});
 assert.equal(parseIntent(data('Buatkan engine translate semua bahasa')),null);
 assert.equal(parseIntent(data('Apakah bisa translate ke Jepang?')),null);
 assert.equal(parseIntent({...data('Translate to Japanese: Good morning'),tools:[{}]}),null);
 assert.equal(parseIntent({messages:[{role:'tool',content:'test'},...data('Translate to English: Halo').messages]}),null);
});
test('ordinary chat and CS never invoke translation',async()=>{
 const x=create({post:()=>{throw Error('unexpected');}});
 for(const [req,d] of [[{headers:{}},data('Halo')],[{headers:{'x-harmonika-client':'customer-agent'}},data('Translate to English: Halo')]])assert.equal(await x.handle(req,response(),d),false);
});
test('translation emits plain completion/SSE and explicit review metadata',async()=>{
 const x=create({post:async()=>({ok:true,text:'Sugeng enjing',needs_review:true,source:'id',target:'jv',quality:'model_self_estimated'})});
 for(const stream of [false,true]){const r=response();assert.equal(await x.handle({headers:{}},r,{...data('Terjemahkan ke Jawa: Selamat pagi'),stream}),true);assert.equal(r.status,200);assert.match(r.body,/Sugeng enjing/);assert.match(r.body,/needs_review/);if(stream)assert.match(r.body,/data: \[DONE\]/);else assert.match(JSON.parse(r.body).choices[0].message.content,/^Sugeng enjing/);}
});
test('provider errors never expose raw text or keys',async()=>{
 const x=create({post:async()=>{throw Error('secret raw prompt');}});const r=response();
 await x.handle({headers:{}},r,data('Translate to English: Halo'));assert.equal(r.status,503);assert.doesNotMatch(r.body,/secret|raw prompt/);
});
