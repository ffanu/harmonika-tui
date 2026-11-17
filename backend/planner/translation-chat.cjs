'use strict';
// Explicit text transformations leave the chat path before shared memory/CS.
const fs = require('node:fs');
const http = require('node:http');
const {randomUUID} = require('node:crypto');
const CS = new Set(['internal-agent','customer-agent','website-cs-autoreply','website-cs-preview']);
function parseIntent(data) {
  if (!data || (data.tools && (!Array.isArray(data.tools) || data.tools.length)) ||
      data.legal || data.telco || data.workspace_id || data.images?.length || data.attachment_ids?.length ||
      (data.tool_choice && data.tool_choice !== 'none' && data.tool_choice !== 'auto') ||
      !Array.isArray(data.messages) || data.messages.some(m => m.role === 'tool' || m.tool_calls?.length)) return null;
  const last = data.messages.at(-1);
  if (last?.role !== 'user' || typeof last.content !== 'string') return null;
  const s = last.content.trim();
  let m = /^(?:tolong\s+)?(?:terjemahkan|translate|translite)\s+(?:dari\s+(?:bahasa\s+)?(.{1,80}?)\s+)?(?:ke|to)\s+(?:bahasa\s+)?([^:\n]{1,80})\s*:\s*([\s\S]+)$/iu.exec(s);
  if (m) return {source:m[1]?.trim() || 'auto', target:m[2].trim(), text:m[3]};
  m = /^(?:tolong\s+)?(?:terjemahkan|translate|translite)\s+["“]([\s\S]+)["”]\s+(?:ke|to)\s+(?:bahasa\s+)?([^\n:]{1,80})[.!]?$/iu.exec(s);
  return m ? {source:'auto',target:m[2].replace(/[.!]$/u,'').trim(),text:m[1]} : null;
}
function privatePost(body) {
  return new Promise((resolve,reject) => {
    let secret;
    try {secret = fs.readFileSync('/etc/harmonika-ai/translation-engine.token','utf8').trim();}
    catch {reject(Error('translation_not_configured'));return;}
    if(secret.length < 16){reject(Error('translation_not_configured'));return;}
    const raw=Buffer.from(JSON.stringify(body));
    if(raw.length>262144){reject(Error('translation_request_too_large'));return;}
    const req=http.request({host:'127.0.0.1',port:17918,path:'/translate',method:'POST',
      headers:{'content-type':'application/json','content-length':raw.length,
        'X-Harmonika-Translation-Secret':secret,'X-Harmonika-Translation-Actor':'core-chat'},
      timeout:125000},res=>{
      const blocks=[];let size=0;
      res.on('data',b=>{size+=b.length;if(size>524288){res.destroy();reject(Error('translation_unavailable'));}else blocks.push(b);});
      res.on('error',()=>reject(Error('translation_unavailable')));
      res.on('end',()=>{try{
        const value=JSON.parse(Buffer.concat(blocks).toString('utf8'));
        if(res.statusCode!==200){const e=Error('translation_failed');e.status=res.statusCode;e.code=/^[a-z][a-z0-9_]{0,63}$/u.test(value.error)?value.error:'translation_unavailable';reject(e);return;}
        if(value.ok!==true||typeof value.text!=='string'||typeof value.needs_review!=='boolean')throw Error();
        resolve(value);
      }catch{reject(Error('translation_unavailable'));}});
    });
    req.on('timeout',()=>{req.destroy();reject(Error('translation_timeout'));});
    req.on('error',()=>reject(Error('translation_unavailable')));
    req.end(raw);
  });
}
function send(res,status,body){res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(body));}
function create({post=privatePost}={}) {
  return {async handle(req,res,data){
    if(CS.has(String(req.headers['x-harmonika-client']||'').toLowerCase()))return false;
    const input=parseIntent(data);if(!input)return false;
    try{
      const out=await post(input);
      res.setHeader('X-Harmonika-Translation','v1');
      const text=out.text+(out.needs_review?'\n\nCatatan: terjemahan ini perlu ditinjau, terutama dialek dan tingkat tutur.':'');
      const meta={source:out.source,target:out.target,needs_review:out.needs_review,quality:out.quality};
      const base={id:'translation-'+randomUUID(),created:Math.floor(Date.now()/1000),model:'harmonika-ai'};
      if(data.stream){
        res.writeHead(200,{'content-type':'text/event-stream; charset=utf-8','cache-control':'no-store','X-Accel-Buffering':'no'});
        for(const delta of [{role:'assistant',content:text},{}])res.write('data: '+JSON.stringify({...base,object:'chat.completion.chunk',translation:meta,choices:[{index:0,delta,finish_reason:Object.keys(delta).length?null:'stop'}]})+'\n\n');
        res.end('data: [DONE]\n\n');
      }else send(res,200,{...base,object:'chat.completion',translation:meta,choices:[{index:0,message:{role:'assistant',content:text},finish_reason:'stop'}]});
    }catch(e){
      const code=/^[a-z][a-z0-9_]{0,63}$/u.test(e.code||'')?e.code:['translation_not_configured','translation_request_too_large','translation_timeout'].includes(e.message)?e.message:'translation_unavailable';
      const status=e.status>=400&&e.status<=599?e.status:code==='translation_request_too_large'?413:code==='translation_timeout'?504:503;
      send(res,status,{error:{code,message:'Layanan terjemahan belum berhasil memproses teks. Silakan coba kembali.'}});
    }
    return true;
  }};
}
module.exports={create,parseIntent};
