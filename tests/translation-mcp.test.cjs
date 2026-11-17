"use strict";
const test=require('node:test'),assert=require('node:assert/strict');
const {createServer}=require('../tools/mcp-harmonika-tools.js');
async function call(server,name,args){return server.handleLine(JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}}));}
test('translation tools route only through shared authenticated HTTP helper',async()=>{
 const requests=[];const server=createServer({request:async(url,body,options)=>{requests.push({url,body,options});return {status:200,value:{ok:true,text:'Wilujeng enjing',needs_review:true}};}});
 const result=await call(server,'translate_text',{text:'Selamat pagi',target:'su',register:'lemes'});
 assert.equal(result.result.isError,undefined);assert.equal(JSON.parse(result.result.content[0].text).text,'Wilujeng enjing');
 assert.equal(requests[0].url,'https://chat.harmonika.id/api/tools/translation/translate');assert.equal(requests[0].options.method,'POST');assert.equal(requests[0].options.timeout,135000);
});
test('malformed translation inputs never invoke HTTP',async()=>{
 let requests=0;const server=createServer({request:async()=>{requests++;throw Error('network must not be called');}});
 for(const [name,args] of [['translate_text',{text:'x',target:'jv',tone:'execute'}],['translate_text',{text:'x',target:'jv',register:'sudo'}],['translate_text',{text:'x'.repeat(40001),target:'en'}],['translate_batch',{items:Array(17).fill({text:'x',target:'en'})}],['translation_languages',{url:'http://private'}]]){
  const result=await call(server,name,args);assert.equal(result.result.isError,true);
 }
 assert.equal(requests,0);
});
test('regional language list is filtered without forwarding caller URLs or query strings',async()=>{
 const server=createServer({request:async(url,body,options)=>{assert.equal(url,'https://chat.harmonika.id/api/tools/translation/languages');return {status:200,value:{ok:true,languages:[{code:'su',regional:true},{code:'en',regional:false}]}};}});
 const result=await call(server,'translation_languages',{regional_only:true});assert.deepEqual(JSON.parse(result.result.content[0].text).languages,[{code:'su',regional:true}]);
});
