// Fixed browser runner. The container has no network/host mounts or GPU device.
const fs=require('node:fs'),crypto=require('node:crypto');
const {chromium}=require('/opt/harmonika/web/node_modules/playwright');
(async()=>{const input=JSON.parse(fs.readFileSync('request.json','utf8'));let browser;
try{browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',headless:true,args:['--disable-gpu','--no-sandbox','--disable-dev-shm-usage']});
const page=await browser.newPage({viewport:input.viewport||{width:1280,height:900}});page.setDefaultTimeout(1200);await page.route('**/*',route=>route.abort());const errors=[];page.on('pageerror',()=>errors.push('page_script_error'));await page.setContent(input.html,{waitUntil:'domcontentloaded',timeout:3000});const results=[];
for(const a of input.actions){const loc=a.selector?page.locator(a.selector):null;
switch(a.type){
case 'dom.query':results.push({type:a.type,count:await loc.count()});break;
case 'dom.text':results.push({type:a.type,text:(await loc.first().innerText()).slice(0,10000)});break;
case 'dom.click':await loc.first().click();results.push({type:a.type,clicked:true});break;
case 'dom.fill':await loc.first().fill(a.value);results.push({type:a.type,filled:true});break;
case 'dom.check':await loc.first().check();results.push({type:a.type,checked:true});break;
case 'dom.select':await loc.first().selectOption(a.value);results.push({type:a.type,selected:true});break;
case 'dom.wait_visible':await loc.first().waitFor({state:'visible'});results.push({type:a.type,visible:true});break;
case 'keyboard.press':await page.keyboard.press(a.value);results.push({type:a.type,pressed:true});break;
case 'dom.scroll':await loc.first().scrollIntoViewIfNeeded();results.push({type:a.type,scrolled:true});break;
default:throw Error('unsupported_action');}}
const shot=await page.screenshot({type:'png'});const data={actions:results,screenshot_sha256:crypto.createHash('sha256').update(shot).digest('hex'),viewport:page.viewportSize(),horizontal_overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),page_errors:errors.length};fs.writeFileSync('result.json',JSON.stringify(data));
}finally{if(browser)await browser.close();}})().catch(()=>{console.error('browser_workflow_failed');process.exitCode=1;});
