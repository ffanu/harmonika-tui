'use strict';
const fs=require('node:fs'),path=require('node:path'),a=require('node:assert/strict'),cp=require('node:child_process');
const root=process.cwd(),config=JSON.parse(fs.readFileSync(path.join(root,'tools/lab.json'))),{chromium}=require(config.browser_module);
const mode=process.argv.includes('--case')?process.argv[process.argv.indexOf('--case')+1]:'smoke';
(async()=>{const out=fs.mkdtempSync(path.join(root,'qa-'+mode+'-')),checks=[],errors=[],check=(name,ok)=>{checks.push({name,passed:!!ok});a.ok(ok,name)};let b,c,server;
try{
 if(process.argv.includes('--local')){
  a.equal(mode,'smoke');server=cp.spawn(path.join(root,'.venv/bin/python'),['tools/serve.py','--db',path.join(out,'local.sqlite')],{cwd:root,stdio:['pipe','pipe','pipe']});
  config.url=await new Promise((resolve,reject)=>{let buf='';server.stdout.on('data',v=>{buf+=v;const i=buf.indexOf('\n');if(i>=0){try{resolve(JSON.parse(buf.slice(0,i)).url)}catch{reject(Error('local_server_invalid_start'))}}});server.once('exit',()=>reject(Error('local_server_exited')))});
 }
 b=await chromium.launch({headless:true});c=await b.newContext({viewport:{width:1280,height:900}});await c.route('**/*',route=>route.request().url().startsWith(config.url+'/')?route.continue():route.abort());const page=await c.newPage();page.setDefaultTimeout(15000);page.on('dialog',d=>d.accept());page.on('pageerror',e=>errors.push(e.name));await page.goto(config.url);await page.locator('#username').fill('alpha');await page.locator('#password').fill('demo-alpha');await page.locator('#login').click();await page.locator('#workspace').waitFor({state:'visible'});
 const title='Browser '+mode+' '+Date.now(),hostile='<img src=x onerror="window.fixtureXss=1">';
 await page.locator('#title').fill(title);await page.locator('#description').fill(hostile);
 if(mode==='lost-ack'||mode==='locked'){
  const fault=cp.spawnSync('python3',['tools/ops.py','fault','--kind',mode==='lost-ack'?'drop-ack':'database-lock'],{cwd:root,encoding:'utf8'});check('fault armed',fault.status===0);
 }
 await page.locator('#create').click();
 if(mode==='locked'){
  await page.locator('#error').filter({hasText:/busy|sibuk|coba|retry|terkunci/i}).waitFor();check('busy surfaced',true);await page.waitForTimeout(8400);await page.locator('#create').click();
 }
 await page.getByText(title,{exact:true}).waitFor();check('create or reconcile visible',true);
 await page.reload();await page.getByText(title,{exact:true}).waitFor();check('refresh persists',true);
 const row=page.locator('#tickets article').filter({has:page.getByText(title,{exact:true})});
 const changed=page.waitForResponse(r=>r.request().method()==='PATCH'&&r.url().includes('/api/tickets/'));await row.locator('.status').selectOption('working');check('patch response', (await changed).status()===200);await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"] .status`)?.value==='working',await row.getAttribute('data-id'));await page.reload();await row.locator('.status').waitFor();check('status persisted',(await row.locator('.status').inputValue())==='working');
 await row.locator('.attachment').setInputFiles({name:'proof.txt',mimeType:'text/plain',buffer:Buffer.from('SYNTHETIC ATTACHMENT')});await row.locator('.upload').click();await row.locator('.attachments a').filter({hasText:'proof.txt'}).waitFor();check('attachment listed',true);
 check('XSS literal',await page.evaluate(()=>!window.fixtureXss));await page.locator('#search').fill('no-match-'+Date.now());await page.waitForFunction(()=>document.querySelectorAll('#tickets article').length===0);check('search hides unrelated',await page.locator('#tickets article').count()===0);await page.locator('#search').fill('');await page.getByText(title,{exact:true}).waitFor();
 await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});await page.setViewportSize({width:360,height:780});await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});check('mobile no overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const tiny=await page.locator('button:visible,input:visible,select:visible').evaluateAll(es=>es.some(e=>e.getBoundingClientRect().height<36));check('touch controls',!tiny);
 await row.locator('.delete').click();await page.getByText(title,{exact:true}).waitFor({state:'hidden'});await page.reload();check('delete persists',await page.getByText(title,{exact:true}).count()===0);
 await page.locator('#logout').click();await page.locator('#login-form').waitFor({state:'visible'});check('logout clears rows',await page.locator('#tickets article').count()===0);check('no page exceptions',errors.length===0);
}catch(e){checks.push({name:'execution',passed:false,error_name:e.name,message:String(e.message).slice(0,180)});process.exitCode=1}
finally{if(mode!=='smoke'){try{await fetch(config.url+'/__exam/disarm',{method:'POST',body:'{}'})}catch{}}if(server){server.stdin.end('shutdown\n');await new Promise(resolve=>server.once('exit',resolve));}if(c)await c.close();if(b)await b.close();const report={case:mode,passed:checks.length>0&&checks.every(x=>x.passed),checks,artifacts:['desktop.png','mobile.png'].filter(n=>fs.existsSync(path.join(out,n))),browser_owned:true};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({out,...report}))}
})();
