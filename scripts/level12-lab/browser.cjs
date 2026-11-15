'use strict';
// Protected examiner browser; credentials exist only in private subprocess stdin.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const root=fs.realpathSync(process.cwd()),config=JSON.parse(fs.readFileSync(root+'/tools/lab.json')),runtime=JSON.parse(fs.readFileSync(root+'/tools/browser-runtime.json')),{chromium}=require(runtime.module);
(async()=>{
 const fixtureProcess=cp.spawnSync('python3',[config.controller,'--browser-fixture'],{encoding:'utf8',timeout:30000});
 if(fixtureProcess.status!==0)throw Error('fixture_not_ready');
 const fixture=JSON.parse(fixtureProcess.stdout);let browser;const checks=[],errors=[],assets=[];
 const out=fs.mkdtempSync(path.join(root,'reports','browser-'));
 const assert=(ok,name)=>{checks.push({name,passed:!!ok});if(!ok)throw Error(name)};
 try{
  browser=await chromium.launch({headless:true});const context=await browser.newContext();
  await context.route('**/*',r=>r.request().url().startsWith(fixture.origin+'/')?r.continue():r.abort());
  const page=await context.newPage();page.setDefaultTimeout(10000);
  page.on('pageerror',()=>errors.push('page_error'));
  page.on('dialog',async d=>{if(d.type()==='confirm'&&d.message()==='Hapus data ini?')await d.accept();else{errors.push('unexpected_dialog');await d.dismiss()}});
  page.on('response',r=>{if(r.url().includes('/static/')&&r.status()>=400)assets.push('http_'+r.status())});
  page.on('requestfailed',req=>{if(req.url().includes('/static/'))assets.push('request_failed')});
  const map=JSON.parse(fs.readFileSync(root+'/UI-MAP.json'));
  const el=k=>{if(!map[k]||typeof map[k]!=='string')throw Error('missing_ui_map_'+k);return page.locator(map[k])};
  const settled=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  async function nav(key){
   if(!await el(key).isVisible())await el('menu_toggle').click();
   await el(key).click();
   if(key==='nav_dashboard'){await page.getByRole('heading',{name:'Dashboard',exact:true}).waitFor();await page.locator('#dashboard-tasks h2').waitFor();await page.locator('#dashboard-articles h2').waitFor();await el('customer_name').waitFor();await page.locator('.metrics strong').last().waitFor();}
   if(key==='nav_customers')await el('customer_name').waitFor();
   if(key==='nav_articles')await el('article_title').waitFor();
   if(key==='nav_tasks')await page.getByRole('heading',{name:'Tugas',exact:true}).first().waitFor();
   await settled();
  }
  await page.goto(fixture.origin);await el('login_username').fill('admin');await el('login_password').fill(fixture.password);
  await el('login_submit').click();await el('nav_dashboard').waitFor();await nav('nav_customers');
  const sentinel='Browser '+Date.now()+' <script>literal</script>';
  await el('customer_name').fill(sentinel);await el('customer_email').fill('browser@example.invalid');await el('customer_stage').selectOption('active');
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/customers');
  await el('customer_submit').click();assert((await created).status()===201,'customer_created_http201');await page.getByText(sentinel,{exact:true}).first().waitFor();assert(true,'real_customer_literal_render');
  const searched=page.waitForResponse(r=>r.request().method()==='GET'&&new URL(r.url()).pathname==='/api/customers'&&new URL(r.url()).searchParams.get('query')===sentinel);
  await el('customer_search').fill(sentinel);await(await searched).finished();await settled();await page.getByText(sentinel,{exact:true}).first().waitFor();assert(true,'customer_search');
  await el('customer_search').fill('');await nav('nav_dashboard');
  for(const [width,height] of [[360,780],[768,900],[1280,900]]){
   await page.setViewportSize({width,height});await settled();
   await page.screenshot({path:path.join(out,'screen-'+width+'.png'),fullPage:true});
   const v=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,brand:document.body.innerText.includes('HARMONIKA'),targets:[...document.querySelectorAll('button,input,select,textarea,a')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.height&&getComputedStyle(e).visibility!=='hidden'}).map(e=>e.getBoundingClientRect().height)}));
   assert(!v.overflow,'overflow_'+width);assert(v.brand,'brand_'+width);assert(v.targets.length>0&&v.targets.every(h=>h>=40),'targets_'+width);
   if(width<900){await nav('nav_articles');await nav('nav_dashboard');assert(true,'mobile_navigation_'+width)}
  }
  await page.setViewportSize({width:1280,height:900});await nav('nav_articles');
  const title='Browser article '+Date.now();await el('article_title').fill(title);await el('article_body').fill('Literal <script> text stays text.');
  const articleCreated=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/articles');
  await el('article_submit').click();assert((await articleCreated).status()===201,'article_created_http201');await page.getByText(title,{exact:true}).first().waitFor();assert(true,'article_saved_list');
  // Hold the older response until the newer query renders; observe every subsequent DOM mutation.
  await nav('nav_customers');let releaseOld,markSeen,markDelivered;
  const gate=new Promise(r=>releaseOld=r),seen=new Promise(r=>markSeen=r),delivered=new Promise(r=>markDelivered=r);
  await page.evaluate(()=>{window.__examStaleSeen=false;window.__examObserver=new MutationObserver(()=>{if(document.body.innerText.includes('STALE-UI-SENTINEL'))window.__examStaleSeen=true});window.__examObserver.observe(document.body,{childList:true,subtree:true,characterData:true})});
  await page.route('**/api/customers?**',async route=>{
   const u=new URL(route.request().url());
   if(u.searchParams.get('query')!=='older'){await route.continue();return}
   try{const response=await route.fetch();markSeen();await gate;await route.fulfill({response,json:{customers:[{id:'a'.repeat(32),name:'STALE-UI-SENTINEL',email:'old@example.invalid',stage:'lead',owner:'admin',revision:1}]}})}finally{markDelivered()}
  });
  await el('customer_search').fill('older');let timeout;
  try{await Promise.race([seen,new Promise((_,reject)=>timeout=setTimeout(()=>reject(Error('old_request_not_seen')),10000))])}finally{clearTimeout(timeout)}
  const latest=page.waitForResponse(r=>r.request().method()==='GET'&&new URL(r.url()).pathname==='/api/customers'&&new URL(r.url()).searchParams.get('query')===sentinel);
  await el('customer_search').fill(sentinel);await(await latest).finished();await settled();await page.getByText(sentinel,{exact:true}).first().waitFor();
  releaseOld();await delivered;await settled();
  assert(!(await page.evaluate(()=>window.__examStaleSeen)),'stale_reply_never_rendered');await page.getByText(sentinel,{exact:true}).first().waitFor();
  await page.evaluate(()=>window.__examObserver.disconnect());await page.unroute('**/api/customers?**');
  // Deleting the currently edited record must leave a usable create form.
  await nav('nav_customers');await page.getByText(sentinel,{exact:true}).first().waitFor();
  const matchingRow=page.getByRole('row').filter({has:page.getByText(sentinel,{exact:true})});
  const record=await matchingRow.count()?matchingRow:page.locator('article.card').filter({has:page.getByRole('heading',{name:sentinel,exact:true})});
  await record.getByRole('button',{name:'Edit',exact:true}).click();
  const deletion=page.waitForResponse(r=>r.request().method()==='DELETE'&&new URL(r.url()).pathname.startsWith('/api/customers/'));
  await record.getByRole('button',{name:'Hapus',exact:true}).click();assert((await deletion).status()===200,'delete_current_edit_http200');await record.waitFor({state:'hidden'});
  const replacement=sentinel+' replacement';await el('customer_name').fill(replacement);await el('customer_email').fill('replacement-browser@example.invalid');await el('customer_stage').selectOption('lead');
  const nextMutation=page.waitForResponse(r=>['POST','PATCH'].includes(r.request().method())&&new URL(r.url()).pathname.startsWith('/api/customers'));
  await el('customer_submit').click();const next=await nextMutation;checks.push({name:'post_delete_edit_observed',passed:next.request().method()==='POST'&&next.status()===201,method:next.request().method(),status:next.status()});
  assert(next.request().method()==='POST'&&next.status()===201,'delete_edit_does_not_trap_creation');await page.getByText(replacement,{exact:true}).first().waitFor();
  await el('logout').click();await el('login_submit').waitFor();assert(true,'logout_returns_login');
  assert(errors.length===0,'no_pageerrors');assert(assets.length===0,'no_failed_static_assets');
  const result={passed:true,checks,actual_browser:true,physical_ime_tested:false,out};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(e){const result={passed:false,checks,error:String(e.message).split(fixture.password).join('[redacted]'),actual_browser:true,out};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));process.exitCode=1}
 finally{if(browser)await browser.close()}
})().catch(e=>{console.log(JSON.stringify({passed:false,error:String(e.message)}));process.exitCode=1});
