'use strict';
// Fixed worker: supplied data are selectors/assertions, never JavaScript to eval.
const fs=require('node:fs'),path=require('node:path');
const send=value=>process.stdout.write(JSON.stringify(value));
(async()=>{
 let raw='';for await(const chunk of process.stdin){raw+=chunk;if(Buffer.byteLength(raw)>65536)throw Error('payload_limit');}
 const input=JSON.parse(raw);
 if(!path.isAbsolute(input.playwright_module))throw Error('runtime_path');
 const runtime=require(input.playwright_module),version=require(path.join(input.playwright_module,'package.json')).version;
 if(process.argv.includes('--capabilities')){send({available:fs.existsSync(runtime.chromium.executablePath()),version});return;}
 if(!/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(input.origin)||!path.isAbsolute(input.artifacts))throw Error('scope');
 let browser,context,page;const rows=[];let blocked_requests=0,blocked_popups=0,blocked_downloads=0,page_errors=0,status='failed';
 try{
  try{browser=await runtime.chromium.launch({headless:true,chromiumSandbox:true,tracesDir:input.trace_temporary,args:['--disable-gpu','--disable-background-networking','--disable-component-update','--no-pings','--dns-prefetch-disable','--force-webrtc-ip-handling-policy=disable_non_proxied_udp']});}
  catch{send({status:'needs_toolchain',steps:[]});return;}
  context=await browser.newContext({viewport:input.viewport,acceptDownloads:false,serviceWorkers:'block',permissions:[]});
  if(typeof context.routeWebSocket!=='function')throw Error('websocket_guard_missing');
  await context.routeWebSocket('**/*',socket=>{blocked_requests++;socket.close();});
  await context.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(url.origin!==input.origin||!['GET','HEAD'].includes(route.request().method())){blocked_requests++;return route.abort('blockedbyclient');}
   return route.continue();
  });
  context.on('page',created=>{if(!page)page=created;else if(created!==page){blocked_popups++;created.close().catch(()=>{});}});
  // Browser APIs cannot be used to establish non-HTTP network connections.
  await context.addInitScript(()=>{for(const key of ['RTCPeerConnection','webkitRTCPeerConnection','WebTransport'])try{Object.defineProperty(globalThis,key,{value:undefined,writable:false,configurable:false});}catch{}});
  page=await context.newPage();page.setDefaultTimeout(1500);page.on('pageerror',()=>{page_errors++;});page.on('console',message=>{if(message.type()==='error')page_errors++;});page.on('download',download=>{blocked_downloads++;download.cancel().catch(()=>{});});
  await context.tracing.start({screenshots:true,snapshots:true,sources:false});
  const response=await page.goto(input.origin+'/'+input.entry,{waitUntil:'domcontentloaded',timeout:10000});
  if(!response||response.status()!==200)throw Error('entry_failed');
  const eventually=async(read,expected)=>{const deadline=Date.now()+1500;do{if(await read()===expected)return;await new Promise(resolve=>setTimeout(resolve,50));}while(Date.now()<deadline);throw Error('assertion');};
  const locate=selector=>selector.by==='test_id'?page.getByTestId(selector.value):selector.by==='role'?page.getByRole(selector.value,{...(selector.name?{name:selector.name,exact:true}:{})}):page.locator('css='+selector.value);
  for(let index=0;index<input.steps.length;index++){
   const step=input.steps[index],loc=locate(step.selector);let passed=false;
   try{
    if(step.action!=='assert_count'&&await loc.count()>1)throw Error('ambiguous_selector');
    switch(step.action){
     case 'click':await loc.click();break;
     case 'fill':{
      const type=await loc.getAttribute('type'),name=await loc.getAttribute('name');
      if(type==='password'||/password|secret|token|credential|api.?key/i.test(name||''))throw Error('credential_field');
      await loc.fill(step.value);break;
     }
     case 'check':await loc.check();break;
     case 'select':await loc.selectOption(step.value);break;
     case 'assert_visible':await loc.waitFor({state:'visible'});break;
     case 'assert_text':await eventually(()=>loc.innerText(),step.value);break;
     case 'assert_value':await eventually(()=>loc.inputValue(),step.value);break;
     case 'assert_count':await eventually(()=>loc.count(),step.count);break;
     default:throw Error('action');
    }
    passed=true;
   }catch{}
   rows.push({index,passed});if(!passed)break;
  }
  status=rows.length===input.steps.length&&rows.every(row=>row.passed)&&!blocked_requests&&!blocked_popups&&!blocked_downloads&&!page_errors?'passed':'failed';
  await page.screenshot({path:path.join(input.artifacts,'screenshot.png'),type:'png',fullPage:false,animations:'disabled',timeout:5000});
 }catch{}finally{
  if(context)try{await context.tracing.stop({path:path.join(input.artifacts,'trace.zip')});}catch{}
  if(context)await context.close().catch(()=>{});
  if(browser)await browser.close().catch(()=>{});
 }
 send({status,steps:rows,blocked_requests:Math.min(blocked_requests,10000),blocked_popups:Math.min(blocked_popups,10000),blocked_downloads:Math.min(blocked_downloads,10000),page_errors:Math.min(page_errors,10000)});
})().catch(()=>{send({status:'failed',steps:[],blocked_requests:0,blocked_popups:0,blocked_downloads:0,page_errors:0});process.exitCode=1;});
