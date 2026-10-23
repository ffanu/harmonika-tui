#!/usr/bin/env node
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const dns = require('node:dns').promises;
const net = require('node:net');
function privateIP(raw) {
  const ip = raw.replace(/^\[|\]$/g, '').toLowerCase();
  if (net.isIP(ip) === 4) {
    const [a,b] = ip.split('.').map(Number);
    return a === 0 || a === 10 || a === 127 || a === 169 && b === 254 || a === 172 && b >= 16 && b <= 31 || a === 192 && b === 168 || a >= 224 || a === 100 && b >= 64 && b <= 127 || a === 198 && (b === 18 || b === 19);
  }
  // Block special/private IPv6 and mapped IPv4; public unicast is 2000::/3.
  return net.isIP(ip) === 6 && !/^[23][0-9a-f]{0,3}:/.test(ip);
}
function localHost(host) { return ['localhost','127.0.0.1','[::1]','::1'].includes(host.toLowerCase()); }
async function validateURL(value, allowLocal, lookup = dns.lookup) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Only HTTP(S) URLs without credentials are supported');
  const host = url.hostname.toLowerCase();
  if (localHost(host)) {
    if (!allowLocal) throw new Error('Localhost request outside authorized development target');
    return url;
  }
  if (!host.includes('.') || /\.(localhost|local|internal|lan|home|test)$/.test(host)) throw new Error('Private hostname blocked');
  const addresses = net.isIP(host.replace(/^\[|\]$/g,'')) ? [{address:host}] : await lookup(host,{all:true});
  if (!addresses.length || addresses.some(a=>privateIP(a.address))) throw new Error('Private network address blocked');
  return url;
}
function safeOut(value) {
  if (!path.isAbsolute(value)) throw new Error('Output directory must be absolute');
  const out=path.resolve(value);
  if (out === path.parse(out).root || out === require('node:os').homedir()) throw new Error('Choose a project output subdirectory');
  let cursor=path.parse(out).root;
  for (const part of out.slice(cursor.length).split(path.sep)) {
    if (part.startsWith('.')) throw new Error('Protected dot-directory blocked');
    cursor=path.join(cursor,part);
    if (fs.existsSync(cursor) && fs.lstatSync(cursor).isSymbolicLink()) throw new Error('Symlink output blocked');
  }
  if (fs.existsSync(out) && (!fs.statSync(out).isDirectory() || fs.readdirSync(out).length)) throw new Error('Output must be a new or empty directory');
  return out;
}
function argumentsFor(argv) {
  const result={};
  for(let i=0;i<argv.length;i+=2) {
    if(!['--url','--out'].includes(argv[i]) || !argv[i+1] || result[argv[i]]) throw new Error('Usage: smoke.cjs --url URL --out ABSOLUTE_EMPTY_DIRECTORY');
    result[argv[i]]=argv[i+1];
  }
  if(!result['--url'] || !result['--out']) throw new Error('Both --url and --out are required');
  return result;
}
async function main(argv) {
  const args=argumentsFor(argv);
  const target=await validateURL(args['--url'],true);
  const allowLocal=localHost(target.hostname);
  const out=safeOut(args['--out']);
  let playwright;
  const override=process.env.HARMONIKA_PLAYWRIGHT_MODULE;
  if(override && !path.isAbsolute(override)) throw new Error('HARMONIKA_PLAYWRIGHT_MODULE must be an absolute trusted module path');
  try {
    if(override) playwright=require(override);
    else {
      try {playwright=require('playwright');}
      catch {playwright=require(path.join(require('node:os').homedir(), '.config/harmonika-terminal/browser-runtime/node_modules/playwright'));}
    }
  } catch { throw new Error('Unsupported: Playwright package missing. Install it explicitly in the project or set HARMONIKA_PLAYWRIGHT_MODULE.'); }
  if(!playwright.chromium || typeof playwright.chromium.launch !== 'function') throw new Error('Unsupported: module does not provide Playwright Chromium');
  let browser;
  const report={url:target.href, checks:[], limitations:['Smoke only; no full accessibility, interaction or visual assertion.', 'DNS validation is best effort; run only trusted sites, not hostile pages or an SSRF security boundary.']};
  try {
    browser=await playwright.chromium.launch({headless:true});
    fs.mkdirSync(out,{recursive:true});
    for(const [name,width,height,isMobile] of [['desktop',1440,900,false],['mobile',390,844,true]]) {
      const context=await browser.newContext({viewport:{width,height},isMobile,hasTouch:isMobile,serviceWorkers:'block'});
      const check={name,width,height,consoleErrors:[],pageErrors:[],httpErrors:[],blockedRequests:[]};
      await context.route('**/*',async route=>{
        try {
          const requestURL=route.request().url();
          if(requestURL.startsWith('data:') || requestURL.startsWith('blob:')) return route.continue();
          await validateURL(requestURL,allowLocal);
          return route.continue();
        } catch(e) {check.blockedRequests.push({url:route.request().url(),error:e.message});return route.abort('blockedbyclient');}
      });
      const page=await context.newPage();
      page.on('console',msg=>{if(msg.type()==='error')check.consoleErrors.push(msg.text());});
      page.on('pageerror',err=>check.pageErrors.push(err.message));
      page.on('response',response=>{if(response.status()>=400)check.httpErrors.push({url:response.url(),status:response.status()});});
      const response=await page.goto(target.href,{waitUntil:'networkidle',timeout:30000});
      check.status=response?.status() ?? null;
      check.title=await page.title();
      check.horizontalOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
      check.screenshot=path.join(out,`${name}.png`);
      await page.screenshot({path:check.screenshot,fullPage:true});
      report.checks.push(check);
      await context.close();
    }
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});
    process.stdout.write(JSON.stringify(report,null,2)+'\n');
    if(report.checks.some(c=>c.status>=400 || c.consoleErrors.length || c.pageErrors.length || c.httpErrors.length || c.blockedRequests.length || c.horizontalOverflow)) process.exitCode=1;
  } finally { if(browser) await browser.close(); }
}
module.exports={privateIP,validateURL,safeOut,argumentsFor,main};
if(require.main===module)main(process.argv.slice(2)).catch(e=>{console.error(`Browser QA failed: ${e.message}`);process.exitCode=2;});
