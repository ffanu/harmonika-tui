const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {wrap}=require('../backend/visual_quality/image-quality-router.cjs');
test('bad anatomy cannot publish or silently fall back to another generator',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'bird-test-'));let fallbacks=0;
 try{const render=wrap(async()=>{fallbacks++},{outputRoot:root,worker:async()=>({ok:false,error:'image_visual_quality_failed',quality_gate:{status:'failed'}})});
  const result=await render('one macaw');assert.equal(result.code,1);assert.equal(fallbacks,0);assert.deepEqual(fs.readdirSync(root),[]);
 }finally{fs.rmSync(root,{recursive:true,force:true})}
});
test('checked image publishes with its actual review metadata',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'bird-test-'));
 try{const render=wrap(async()=>{throw Error('unexpected fallback')},{outputRoot:root,publicHost:'https://example.invalid',validatePNG:()=>({width:2048,height:2048}),worker:async body=>{
  const file=path.join(body.output_dir,'checked.png');fs.writeFileSync(file,Buffer.alloc(3*1024*1024));
  return {ok:true,file,model:'FLUX.2 Klein 4B',quality_gate:{status:'passed',observed_heads:1},generation_attempts:2,quality_verification:'bird_count_and_visible_anatomy_checked'};
 }});const result=await render('seekor burung');assert.equal(result.code,0);
 const manifest=JSON.parse(fs.readFileSync(path.join(root,fs.readdirSync(root)[0],'manifest.json')));assert.equal(manifest.quality_gate.observed_heads,1);assert.equal(manifest.generation_attempts,2);
 }finally{fs.rmSync(root,{recursive:true,force:true})}
});
test('unrelated requests preserve the original renderer',async()=>{
 let count=0;const render=wrap(async()=>{count++;return {code:0}});await render('landscape');await render('bird edit',['existing-reference']);assert.equal(count,2);
});
test('unverified successful-looking output cannot be published',async()=>{
 const render=wrap(async()=>{throw Error('fallback')},{worker:async()=>({ok:true,quality_gate:{status:'needs_review'}})});
 assert.equal((await render('a bird')).code,1);
});
