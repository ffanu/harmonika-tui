'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const {spawn}=require('node:child_process');
(async()=>{
  const temp=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-browser-live-')));
  const output=path.join(temp,'screenshots');
  const server=http.createServer((req,res)=>{
    res.writeHead(200,{'Content-Type':'text/html'});
    res.end('<!doctype html><html lang="id"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Harmonika smoke fixture</title><style>body{font-family:sans-serif;margin:24px}main{max-width:50rem}button{padding:12px}</style><main><h1>Harmonika QA</h1><label for="name">Nama</label><input id="name"><button type="button">Lanjut</button></main></html>');
  });
  try{
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const url=`http://127.0.0.1:${server.address().port}`;
    const script=path.resolve(__dirname,'../skills/harmonika-browser-qa/scripts/smoke.cjs');
    const child=spawn(process.execPath,[script,'--url',url,'--out',output],{stdio:['ignore','pipe','pipe']});
    let stdout='',stderr='';
    child.stdout.on('data',x=>stdout+=x);
    child.stderr.on('data',x=>stderr+=x);
    const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',resolve);});
    assert.equal(code,0,stderr+'\n'+stdout);
    const report=JSON.parse(fs.readFileSync(path.join(output,'report.json'),'utf8'));
    assert.equal(report.checks.length,2);
    for(const check of report.checks){
      assert.equal(check.status,200);
      assert.equal(check.title,'Harmonika smoke fixture');
      assert.equal(check.horizontalOverflow,false);
      assert.equal(check.pageErrors.length,0);
      const png=fs.readFileSync(check.screenshot);
      assert.equal(png.subarray(1,4).toString(),'PNG');
      assert.equal(png.readUInt32BE(16),check.width);
      assert.equal(png.readUInt32BE(20),check.height);
    }
    console.log('Actual Chromium smoke passed: 1440x900 desktop and 390x844 mobile; PNG dimensions and browser result verified.');
  }finally{
    await new Promise(resolve=>server.close(resolve));
    fs.rmSync(temp,{recursive:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
