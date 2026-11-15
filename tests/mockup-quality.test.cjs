'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {execFile}=require('node:child_process'),{promisify}=require('node:util');
const execute=promisify(execFile),script=path.resolve(__dirname,'../skills/harmonika-browser-qa/scripts/qa_matrix.cjs');
const {framesFor,MATRIX,validateURL}=require(script);

test('default audit includes the previously missed tablet width',()=>{
 assert.ok(MATRIX.some(p=>p.width===768));assert.equal(framesFor(undefined),MATRIX);
});
test('capture frame input is bounded and rejects malformed contracts',()=>{
 const frame={name:'desktop',width:1440,height:1080,fit:true,requiredSelectors:['h1','footer']};
 assert.equal(framesFor(JSON.stringify([frame]))[0].captureFrame,true);
 for(const input of ['{}','invalid','[]',JSON.stringify([frame,frame]),JSON.stringify([{...frame,width:1440.1}]),
  JSON.stringify([{...frame,width:4096,height:16384}]),JSON.stringify([{...frame,requiredSelectors:['x'.repeat(161)]}]),
  JSON.stringify([{...frame,fit:'true'}])])assert.throws(()=>framesFor(input),/invalid_capture_frames/);
});
test('new screenshot mode preserves the existing URL firewall',async()=>{
 await assert.rejects(validateURL('file:///etc/passwd',true));
 await assert.rejects(validateURL('http://192.168.0.69',true));
 await assert.rejects(validateURL('http://name:secret@localhost',true));
});

test('real browser verifies exact PNGs, clipping, headings and runtime errors',async()=>{
 const directory=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-mockup-test-')));
 const html=(body,css='',js='')=>'<!doctype html><html lang="id"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fixture</title><style>body{margin:16px;color:#102743;background:white;font:14px Arial}'+css+'</style><body><h1>ERP fixture</h1>'+body+'<script>'+js+'</script></body></html>';
 const fixtures={
  '/good':html('<article>KPI fixture</article><p style="width:40px;overflow-wrap:anywhere">September</p><footer>Footer</footer>'),
  '/broken':html('<article>KPI fixture</article><footer>Footer</footer>','body{min-width:1000px}footer{margin-top:1200px}','throw Error("synthetic_runtime_failure")'),
  '/clipped':html('<div style="height:10px;overflow:hidden"><footer style="height:50px">Footer</footer></div>'),
  '/split':html('<div class="bar" style="width:40px;overflow-wrap:anywhere">September</div><footer>Footer</footer>'),
 };
 const server=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end(fixtures[req.url]||fixtures['/good'])});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  const run=async(name,requiredSelectors)=>{
   const out=path.join(directory,name),frames=[{name:'mobile',width:390,height:800,fit:true,requiredSelectors}];
   await execute(process.execPath,[script,'--url',`http://127.0.0.1:${server.address().port}/${name}`,'--out',out,'--frames',JSON.stringify(frames)],{timeout:45000});
   return JSON.parse(fs.readFileSync(path.join(out,'report.json')));
  };
  const good=await run('good',['h1','article','footer']);
  assert.equal(good.verdict,'pass');assert.deepEqual(good.devices[0].image,{width:390,height:800});
  assert.equal(good.devices[0].audit.h1Count,1);assert.equal(good.devices[0].checks.find(x=>x.id==='single-h1').ok,true);
  const broken=await run('broken',['footer','.missing']);
  assert.equal(broken.verdict,'needs-work');
  for(const id of ['frame-content-fits','frame-region:footer','frame-region:.missing','no-browser-errors','no-horizontal-overflow'])
   assert.equal(broken.devices[0].checks.find(x=>x.id===id).ok,false,id);
  assert.equal(broken.devices[0].checks.find(x=>x.id==='capture-pixels-exact').ok,true,'exact dimensions alone cannot pass bad layout');
  const clipped=await run('clipped',['footer']);
  assert.equal(clipped.devices[0].checks.find(x=>x.id==='frame-content-fits').ok,true);
  assert.equal(clipped.devices[0].checks.find(x=>x.id==='frame-region:footer').ok,false,'ancestor clipping must be detected');
  const split=await run('split',['.bar','footer']);
  assert.equal(split.devices[0].checks.find(x=>x.id==='no-split-ui-labels').ok,false,'a whole word split across chart lines is not readable UI');
 }finally{
  await new Promise(resolve=>server.close(resolve));fs.rmSync(directory,{recursive:true,force:true});
 }
});
