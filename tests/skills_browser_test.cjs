'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {validateURL,privateIP,safeOut,argumentsFor}=require('../skills/harmonika-browser-qa/scripts/smoke.cjs');
(async()=>{
  for(const ip of ['127.0.0.1','10.2.0.3','172.16.0.1','192.168.1.1','169.254.1.1','100.64.0.1','::1','::ffff:127.0.0.1','fe80::1','fc00::1'])assert.equal(privateIP(ip),true,ip);
  for(const url of ['file:///etc/passwd','http://user:secret@example.com','http://192.168.0.1','http://internal','http://a.local']) await assert.rejects(()=>validateURL(url,false));
  await assert.rejects(()=>validateURL('http://localhost:3000',false));
  assert.equal((await validateURL('http://localhost:3000',true)).hostname,'localhost');
  await assert.rejects(()=>validateURL('https://public.example',false,async()=>[{address:'10.1.1.1'}]));
  assert.equal((await validateURL('https://public.example',false,async()=>[{address:'8.8.8.8'}])).hostname,'public.example');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-browser-'));
  const resolved=fs.realpathSync(root);
  try{
    assert.equal(safeOut(path.join(resolved,'new')),path.join(resolved,'new'));
    assert.throws(()=>safeOut('relative'));
    assert.throws(()=>safeOut(path.join(resolved,'.ssh','x')));
    fs.writeFileSync(path.join(root,'existing'),'unchanged');
    assert.throws(()=>safeOut(root));
    fs.symlinkSync(resolved,path.join(root,'link'));
    assert.throws(()=>safeOut(path.join(resolved,'link','new')));
    assert.throws(()=>argumentsFor(['--url','http://localhost','--extra','x']));
    assert.throws(()=>argumentsFor(['--url','http://localhost']));
    assert.equal(argumentsFor(['--url','http://localhost','--out',root])['--out'],root);
  }finally{fs.rmSync(root,{recursive:true});}
  console.log('Browser argument, network-policy and filesystem safety tests passed. No actual browser coverage claimed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
