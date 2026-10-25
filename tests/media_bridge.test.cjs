'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createMediaBridge,MediaError,BASE}=require('../runtime/tools/media_bridge.cjs');
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/jXQAAAAASUVORK5CYII=','base64');
function fixture(options={}){
 const calls=[];const artifact={file_id:'file_fixture',mime_type:'image/png',size_bytes:png.length,sha256:crypto.createHash('sha256').update(png).digest('hex'),width:1,height:1,...options.artifact};let polls=0;
 const fetch=async(url,init)=>{
  calls.push({url,...init});assert.equal(init.redirect,'error');assert.equal(init.headers.Authorization,'Bearer fixture-key');assert.ok(url.startsWith(BASE));
  if(options.throwPost&&init.method==='POST')throw new Error('ECONNRESET private');
  if(url.endsWith('/capabilities'))return new Response(JSON.stringify(options.caps||{features:{image_generation:true,image_analysis:false},queue_ready:true}),{headers:{'content-type':'application/json'},status:options.authStatus||200});
  if(url.endsWith('/jobs'))return new Response(JSON.stringify({job:{job_id:'job_fixture',status:options.pending?'queued':options.status||'completed',result:{artifacts:[artifact]}}}),{status:202});
  if(url.endsWith('/jobs/job_fixture'))return new Response(JSON.stringify({job:{job_id:options.wrongID?'job_wrong':'job_fixture',status:++polls<2?'running':'completed',result:{artifacts:[artifact]}}}));
  if(url.endsWith('/files/file_fixture'))return new Response(png,{headers:{'content-type':'image/png'}});
  throw new Error('unexpected route');
 };
 let now=0;const bridge=createMediaBridge({localVisionOnly:false,fetch,key:()=> 'fixture-key',sleep:async ms=>{now+=ms},clock:()=>now});return {calls,bridge};
}
test('authenticated generate downloads verified image and stable idempotency',async()=>{const {bridge,calls}=fixture();const a=await bridge.generate('Blue card','/tmp/blue.png');const b=await bridge.generate('Blue card','/tmp/blue.png');assert.equal(a.provider,'Harmonika Ai');assert.deepEqual(Buffer.from(a.image_base64,'base64'),png);const posts=calls.filter(c=>c.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].headers['Idempotency-Key'],posts[1].headers['Idempotency-Key']);assert.equal(posts[0].headers['Idempotency-Key'].length,64);bridge.close();});
test('offline/unsupported capability does not submit job',async()=>{const {bridge,calls}=fixture({caps:{features:{image_generation:false,image_analysis:false},queue_ready:true}});assert.deepEqual(await bridge.generate('test','/tmp/out.png'),{handled:false,not_dispatched:true});assert.deepEqual(await bridge.analyze('AAAA','test'),{handled:false,not_dispatched:true});assert.equal(calls.filter(c=>c.method==='POST').length,0);});
test('async queued job uses GET polling rather than repeating POST',async()=>{const {bridge,calls}=fixture({pending:true});assert.equal((await bridge.generate('test','/tmp/out.png')).status,'ok');assert.equal(calls.filter(c=>c.method==='POST').length,1);assert.equal(calls.filter(c=>c.url.endsWith('/jobs/job_fixture')).length,2);});
test('ambiguous POST is not retried or silently switched',async()=>{const {bridge,calls}=fixture({throwPost:true});await assert.rejects(bridge.generate('test','/tmp/out.png'),e=>e.code==='media_submission_unknown'&&e.uncertain);assert.equal(calls.filter(c=>c.method==='POST').length,1);});
test('uncertain remote state is an explicit error',async()=>{const {bridge}=fixture({status:'unknown'});await assert.rejects(bridge.generate('test','/tmp/out.png'),e=>e.code==='media_job_uncertain'&&e.uncertain);});
test('checksum and path injection are rejected',async()=>{for(const artifact of [{sha256:'0'.repeat(64)},{file_id:'file_../../etc/passwd'}]){const {bridge}=fixture({artifact});await assert.rejects(bridge.generate('test','/tmp/out.png'),e=>['image_checksum_mismatch','invalid_image_artifact'].includes(e.code));}});
test('foreign/mismatched job never accepted',async()=>{const {bridge}=fixture({pending:true,wrongID:true});await assert.rejects(bridge.generate('test','/tmp/out.png'),e=>e.code==='media_job_identity_mismatch');});
test('invalid auth is not bypassed and does not submit',async()=>{const {bridge,calls}=fixture({authStatus:403,caps:{error:{code:'invalid_token'}}});await assert.rejects(bridge.generate('test','/tmp/out.png'),e=>e.code==='invalid_token');assert.equal(calls.filter(c=>c.method==='POST').length,0);});
test('closed client and oversized prompt are safe errors',async()=>{const {bridge}=fixture();await assert.rejects(bridge.generate('x'.repeat(12001),'/tmp/out.png'),e=>e.code==='invalid_prompt');bridge.close();await assert.rejects(bridge.capabilities(),e=>e.code==='media_client_closed');});

test('supported vision uploads reference and returns text without a second render',async()=>{
 let seq=0;const requests=[];
 const bridge=createMediaBridge({key:()=> 'fixture',fetch:async(url,init)=>{requests.push({url,...init});if(url.endsWith('/capabilities'))return new Response(JSON.stringify({features:{image_analysis:true},queue_ready:true}));if(url.endsWith('/files'))return new Response(JSON.stringify({file:{file_id:'file_ref'+(++seq)}}),{status:201});if(url.endsWith('/jobs'))return new Response(JSON.stringify({job:{job_id:'job_vision',status:'completed',result:{text:'blue card'}}}),{status:202});throw new Error('unexpected');}});
 assert.equal((await bridge.analyze(png.toString('base64'),'describe')).analysis,'blue card');assert.equal((await bridge.analyze(png.toString('base64'),'describe')).analysis,'blue card');const jobs=requests.filter(x=>x.url.endsWith('/jobs'));assert.equal(jobs.length,2);assert.notEqual(jobs[0].headers['Idempotency-Key'],jobs[1].headers['Idempotency-Key']);assert.deepEqual(JSON.parse(jobs[0].body).reference_file_ids,['file_ref1']);bridge.close();
});
test('server submission failure is uncertain and never resubmitted',async()=>{
 let posts=0;const bridge=createMediaBridge({key:()=> 'fixture',fetch:async(url,init)=>{if(init.method==='POST'){posts++;return new Response(JSON.stringify({error:{code:'media_unavailable'}}),{status:503});}return new Response(JSON.stringify({features:{image_generation:true},queue_ready:true}));}});await assert.rejects(bridge.generate('test','/tmp/result.png'),e=>e.uncertain===true);assert.equal(posts,1);bridge.close();
});
test('offline preflight may switch before dispatch, including vision capability probe',async()=>{
 const bridge=createMediaBridge({localVisionOnly:false,key:()=> 'fixture',fetch:async()=>{throw new Error('offline');}});
 assert.deepEqual(await bridge.generate('test','/tmp/offline.png'),{handled:false,not_dispatched:true});
 assert.deepEqual(await bridge.analyze(png.toString('base64'),'describe'),{handled:false,not_dispatched:true});bridge.close();
});
test('network failure while polling preserves uncertain job instead of fallback',async()=>{
 const bridge=createMediaBridge({key:()=> 'fixture',sleep:async()=>{},fetch:async(url)=>{if(url.endsWith('/capabilities'))return new Response(JSON.stringify({features:{image_generation:true},queue_ready:true}));if(url.endsWith('/jobs'))return new Response(JSON.stringify({job:{job_id:'job_pending',status:'queued'}}),{status:202});throw new Error('offline during poll');}});
 await assert.rejects(bridge.generate('test','/tmp/pending.png'),e=>e.uncertain===true);bridge.close();
});

test('local-only vision never falls back to a mentor when offline or unsupported',async()=>{
 for(const down of [true,false]){let posts=0;const bridge=createMediaBridge({key:()=> 'fixture',fetch:async(_,init)=>{if(init.method==='POST')posts++;if(down)throw new Error('offline');return new Response(JSON.stringify({features:{image_analysis:false},queue_ready:true}));}});await assert.rejects(bridge.analyze(png.toString('base64'),'describe'),e=>e.code==='local_vision_unavailable');assert.equal(posts,0);bridge.close();}
});
