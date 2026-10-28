'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {createClient}=require('../backend/remote_access/client.cjs');
test('remote Node adapter refuses non-loopback endpoints',()=>{for(const baseURL of ['http://example.com:18231','https://127.0.0.1','http://user@127.0.0.1'])assert.throws(()=>createClient({baseURL}));});
test('concurrent calls share one authenticated session; no credential in bodies',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-client-')),file=path.join(root,'token');fs.writeFileSync(file,'private-fixture',{mode:0o600});let sessions=0,commands=0;
 const server=http.createServer(async(req,res)=>{let raw='';for await(const c of req)raw+=c;assert.equal(req.headers.authorization,'Bearer private-fixture');assert.ok(!raw.includes('private-fixture'));res.setHeader('Content-Type','application/json');if(req.url.endsWith('/sessions')){sessions++;res.end(JSON.stringify({ok:true,session_id:'fixture'}));}else{assert.equal(req.headers['x-harmonika-session'],'fixture');commands++;res.end(JSON.stringify({ok:true}));}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));try{const client=createClient({baseURL:'http://127.0.0.1:'+server.address().port,tokenFile:file});const replies=await Promise.all(Array.from({length:8},()=>client.test({target:'stage'})));assert.ok(replies.every(r=>r.ok));assert.equal(sessions,1);assert.equal(commands,8);}finally{await new Promise(r=>server.close(r));fs.rmSync(root,{recursive:true});}
});
