'use strict';
// No direct HTTP is continued: the Python broker resolves, validates and pins DNS.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),readline=require('node:readline');
function engine(){const value=process.env.HARMONIKA_PLAYWRIGHT_MODULE||path.join(os.homedir(),'.config/harmonika-terminal/browser-runtime/node_modules/playwright');if(!path.isAbsolute(value))throw Error('invalid_browser_runtime');return require(value);}
if(process.argv.includes('--capabilities')){try{const p=engine();process.stdout.write(JSON.stringify({available:fs.existsSync(p.chromium.executablePath())}));}catch{process.stdout.write('{"available":false}');}process.exit(0);}
let resolveInit;const initialized=new Promise(r=>resolveInit=r);const pending=new Map();let counter=0;
const rl=readline.createInterface({input:process.stdin});
rl.on('line',line=>{try{const msg=JSON.parse(line);if(msg.type==='init')resolveInit(msg);else if(pending.has(msg.id)){pending.get(msg.id)(msg);pending.delete(msg.id);}}catch{}});
function emit(msg){process.stdout.write(JSON.stringify(msg)+'\n');}
async function resource(url){const id=++counter;if(id>300)return {error:'resource_limit'};return new Promise(resolve=>{const timer=setTimeout(()=>{pending.delete(id);resolve({error:'broker_timeout'});},12000);pending.set(id,msg=>{clearTimeout(timer);resolve(msg);});emit({type:'fetch',id,url});});}
(async()=>{const args=await initialized;const out=args.output_dir;if(!path.isAbsolute(out))throw Error('invalid_output');fs.mkdirSync(out,{mode:0o700});let browser;const checks=[];
try{browser=await engine().chromium.launch({headless:true,args:['--disable-gpu','--disable-background-networking','--disable-component-update','--no-pings','--dns-prefetch-disable','--disable-features=NetworkPrediction,SpeculationRulesPrefetch,Prerender2']});
for(const [name,width,height,mobile] of [['desktop',1440,900,false],['mobile',390,844,true]]){emit({type:'viewport',name});const check={name,width,height,blocked_resources:0};const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,javaScriptEnabled:false,serviceWorkers:'block'});
try{await context.route('**/*',async route=>{const url=route.request().url();if(url.startsWith('data:'))return route.continue();if(!/^https?:\/\//i.test(url)||route.request().method()!=='GET'){check.blocked_resources++;return route.abort();}const reply=await resource(url);if(reply.error){check.blocked_resources++;return route.abort();}try{return await route.fulfill({status:reply.status,headers:reply.headers,body:Buffer.from(reply.body_base64||'','base64')});}catch{check.blocked_resources++;return route.abort();}});
const page=await context.newPage();const result=await page.goto(args.url,{waitUntil:'domcontentloaded',timeout:25000});check.http_status=result?.status()||null;
try{await page.waitForLoadState('load',{timeout:4000});}catch{check.load_incomplete=true;}
if(check.http_status>=200&&check.http_status<400){check.title=await page.title();check.horizontal_overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);check.screenshot=path.join(out,name+'.png');await page.screenshot({path:check.screenshot,fullPage:false,animations:'disabled',timeout:12000});}else check.error='page_http_error';
}catch{check.error='capture_failed';}finally{checks.push(check);await context.close();}}
}finally{if(browser)await browser.close();}
emit({type:'result',result:{schema_version:1,status:'degraded',method:'brokered_browser_render_javascript_disabled',checks,limitations:['JavaScript is disabled; SPA behavior and interaction not measured','Viewport captures only, not full-page or Core Web Vitals','All HTTP resources are fulfilled through the bounded SSRF-safe broker; blocked resources can affect appearance']}});rl.close();
})().catch(()=>{emit({type:'error',code:'visual_capture_unavailable'});rl.close();process.exitCode=2;});
