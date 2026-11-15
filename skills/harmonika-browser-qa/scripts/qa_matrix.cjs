#!/usr/bin/env node
'use strict';
/** Harmonika QA matrix: responsive + accessibility audit of an authorized web app.
 *
 * Emulated devices only (Chromium), never real hardware. Read-only: it navigates,
 * measures and screenshots. It never submits credentials, never bypasses a login
 * wall, and never fabricates evidence. Output is JSON plus per-device screenshots.
 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const dns = require('node:dns').promises;
const {execFile}=require('node:child_process');
const execute=require('node:util').promisify(execFile);

const MATRIX = [
  { name: 'desktop-1440', width: 1440, height: 900, mobile: false, locale: 'en-US', scheme: 'light' },
  { name: 'desktop-dark-1280', width: 1280, height: 800, mobile: false, locale: 'id-ID', scheme: 'dark' },
  { name: 'tablet-834', width: 834, height: 1112, mobile: true, locale: 'en-US', scheme: 'light' },
  { name: 'tablet-768', width: 768, height: 1024, mobile: true, locale: 'id-ID', scheme: 'light' },
  { name: 'mobile-390', width: 390, height: 844, mobile: true, locale: 'id-ID', scheme: 'light' },
  { name: 'mobile-small-360', width: 360, height: 640, mobile: true, locale: 'id-ID', scheme: 'dark' },
];
const MIN_TAP = 24;
const MIN_CONTRAST = 4.5;

function privateIP(raw) {
  const ip = raw.replace(/^\[|\]$/g, '').toLowerCase();
  if (net.isIP(ip) === 4) {
    const [a, b] = ip.split('.').map(Number);
    return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || a >= 224 ||
      (a === 100 && b >= 64 && b <= 127) || (a === 198 && (b === 18 || b === 19));
  }
  return net.isIP(ip) === 6 && !/^[23][0-9a-f]{0,3}:/.test(ip);
}

function isLocal(host) {
  return ['localhost', '127.0.0.1', '[::1]', '::1'].includes(host.toLowerCase());
}

function reportURL(value) {
  try { const url=new URL(value);return url.origin+url.pathname; }
  catch { return 'invalid_url'; }
}

async function validateURL(value, allowLocal, lookup = dns.lookup) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('Only HTTP(S) URLs without embedded credentials are supported');
  }
  const host = url.hostname.toLowerCase();
  if (isLocal(host)) {
    if (!allowLocal) throw new Error('Localhost target not authorized for this run');
    return url;
  }
  if (!host.includes('.') || /\.(localhost|local|internal|lan|home|test)$/.test(host)) {
    throw new Error('Private hostname blocked');
  }
  const bare = host.replace(/^\[|\]$/g, '');
  const addresses = net.isIP(bare) ? [{ address: bare }] : await lookup(host, { all: true });
  if (!addresses.length || addresses.some((entry) => privateIP(entry.address))) {
    throw new Error('Private network address blocked');
  }
  return url;
}

function safeOut(value) {
  if (!path.isAbsolute(value)) throw new Error('--out must be an absolute path');
  const out = path.resolve(value);
  if (out === path.parse(out).root || out === os.homedir()) {
    throw new Error('Choose a dedicated output subdirectory');
  }
  let cursor = path.parse(out).root;
  for (const part of out.slice(cursor.length).split(path.sep)) {
    if (part.startsWith('.')) throw new Error('Protected dot-directory blocked');
    cursor = path.join(cursor, part);
    if (fs.existsSync(cursor) && fs.lstatSync(cursor).isSymbolicLink()) {
      throw new Error('Symlink output blocked');
    }
  }
  if (fs.existsSync(out) && (!fs.statSync(out).isDirectory() || fs.readdirSync(out).length)) {
    throw new Error('Output directory must be new or empty');
  }
  return out;
}

function argumentsFor(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    if (!['--url', '--out', '--frames'].includes(key) || !argv[i + 1] || result[key]) {
      throw new Error('Usage: qa_matrix.cjs --url URL --out ABSOLUTE_EMPTY_DIRECTORY [--frames JSON]');
    }
    result[key] = argv[i + 1];
  }
  if (!result['--url'] || !result['--out']) throw new Error('Both --url and --out are required');
  return result;
}

function framesFor(raw) {
  if (raw === undefined) return MATRIX;
  if (typeof raw !== 'string' || Buffer.byteLength(raw) > 12000) throw Error('invalid_capture_frames');
  let frames;try { frames=JSON.parse(raw); } catch { throw Error('invalid_capture_frames'); }
  if (!Array.isArray(frames) || frames.length < 1 || frames.length > 8) throw Error('invalid_capture_frames');
  const names=new Set();
  return frames.map((frame) => {
    if (!frame || typeof frame !== 'object' || Array.isArray(frame) ||
        !/^[a-zA-Z0-9_-]{1,40}$/.test(frame.name || '') || names.has(frame.name) ||
        !Number.isInteger(frame.width) || frame.width<320 || frame.width>4096 ||
        !Number.isInteger(frame.height) || frame.height<320 || frame.height>16384 ||
        frame.width*frame.height>16777216 ||
        (frame.fit!==undefined && typeof frame.fit!=='boolean') ||
        !Array.isArray(frame.requiredSelectors) || frame.requiredSelectors.length>30 ||
        frame.requiredSelectors.some(s=>typeof s!=='string'||!s.length||s.length>160)) throw Error('invalid_capture_frames');
    names.add(frame.name);
    return {name:frame.name,width:frame.width,height:frame.height,mobile:frame.width<900,
      locale:'id-ID',scheme:'light',captureFrame:true,fit:frame.fit===true,requiredSelectors:frame.requiredSelectors};
  });
}

function browserConfig() {
  const file='/etc/harmonika-ai/mockup-browser.json';
  if(process.platform!=='linux'||!fs.existsSync(file))return {};
  if(fs.statSync(file).size>4096)throw Error('invalid_browser_config');
  const value=JSON.parse(fs.readFileSync(file,'utf8'));
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error('invalid_browser_config');
  return value;
}

function loadPlaywright() {
  const override = process.env.HARMONIKA_PLAYWRIGHT_MODULE;
  if (override && !path.isAbsolute(override)) {
    throw new Error('HARMONIKA_PLAYWRIGHT_MODULE must be an absolute trusted module path');
  }
  const configured=browserConfig().modulePath;
  if(configured){
    if(typeof configured!=='string'||!path.isAbsolute(configured))throw Error('invalid_browser_module');
    const module=require(configured);
    if(!module.chromium||typeof module.chromium.launch!=='function')throw Error('invalid_browser_module');
    return module;
  }
  const candidates = [override, 'playwright',
    path.join(os.homedir(), '.config/harmonika-terminal/browser-runtime/node_modules/playwright'),
    '/opt/harmonika-ai/seo-shared/browser/node_modules/playwright']
    .filter(Boolean);
  for (const candidate of candidates) {
    try {
      const module = require(candidate);
      if (module.chromium && typeof module.chromium.launch === 'function') return module;
    } catch (error) { /* try next candidate */ }
  }
  throw new Error('Unsupported: Playwright runtime missing. Install it explicitly in the project.');
}

function browserOptions() {
  let executable=process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  if(!executable)executable=browserConfig().executablePath;
  if(executable && (typeof executable!=='string'||!path.isAbsolute(executable)||!fs.existsSync(executable)||!fs.statSync(executable).isFile()))throw Error('invalid_browser_executable');
  return {headless:true,timeout:20000,...(executable?{executablePath:executable}:{}),
    ...(process.platform==='linux'?{args:['--disable-gpu','--disable-dev-shm-usage']}:{})};
}

async function capturePNG(page, device, audit, shot) {
  const method=browserConfig().captureMethod || 'screenshot';
  if(method==='screenshot'){
    await page.screenshot({path:shot,fullPage:!device.captureFrame,timeout:15000});
    return 'chromium-screenshot';
  }
  if(method!=='pdf-raster'||process.platform!=='linux')throw Error('unsupported_capture_method');
  // Full-page exports allow a small paper-rounding gutter; fixed contracts remain exact.
  const height=device.captureFrame?device.height:Math.ceil(Math.max(device.height,audit.documentHeight))+4;
  const dpr=device.captureFrame?1:device.mobile?2:1;
  if(height>16384||device.width*height*dpr*dpr>67108864)throw Error('capture_dimensions_exceeded');
  const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-mockup-raster-'));fs.chmodSync(temporary,0o700);
  try{
    await page.emulateMedia({media:'screen'});
    await page.addStyleTag({content:'*{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}'});
    const pdf=await page.pdf({width:device.width+'px',height:height+'px',printBackground:true,
      margin:{top:0,right:0,bottom:0,left:0},preferCSSPageSize:false,scale:1,timeout:15000});
    if(pdf.length>64*1024*1024)throw Error('capture_result_too_large');
    const file=path.join(temporary,'frame.pdf');fs.writeFileSync(file,pdf,{mode:0o600});
    const info=await execute('/usr/bin/pdfinfo',[file],{timeout:15000,maxBuffer:65536});
    if(!/^Pages:\s+1\s*$/m.test(info.stdout))throw Error('pdf_capture_pagination_mismatch');
    const prefix=path.join(temporary,'frame');
    await execute('/usr/bin/pdftoppm',['-singlefile','-f','1','-png','-r',String(96*dpr),
      '-scale-to-x',String(device.width*dpr),'-scale-to-y',String(height*dpr),file,prefix],{timeout:15000,maxBuffer:65536});
    fs.copyFileSync(prefix+'.png',shot,fs.constants.COPYFILE_EXCL);fs.chmodSync(shot,0o600);
    return 'chromium-screen-pdf-raster';
  }finally{fs.rmSync(temporary,{recursive:true,force:true})}
}

const PAGE_AUDIT = (options = {}) => {
  const doc = document.scrollingElement || document.documentElement;
  const vw = window.innerWidth;
  const style = (el) => getComputedStyle(el);
  const rect = (el) => el.getBoundingClientRect();
  const visible = (el) => {
    const box=rect(el), computed=style(el);
    return box.width>0 && box.height>0 && computed.visibility!=='hidden' && computed.display!=='none';
  };
  const parseColor = (value) => {
    const match = value.match(/rgba?\(([^)]+)\)/);
    if (!match) return null;
    const parts = match[1].split(',').map((n) => parseFloat(n.trim()));
    if (parts.length < 3) return null;
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };
  const luminance = ({ r, g, b }) => {
    const channel = (value) => {
      const c = value / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  };
  const background = (element) => {
    let node = element;
    while (node && node !== document.documentElement) {
      const colour = style(node).backgroundColor;
      const parsed = colour && colour !== 'transparent' ? parseColor(colour) : null;
      if (parsed && parsed.a > 0.5) return parsed;
      node = node.parentElement;
    }
    return { r: 255, g: 255, b: 255 };
  };
  const contrast = (fg, bg) => {
    const l1 = luminance(fg);
    const l2 = luminance(bg);
    const light = Math.max(l1, l2);
    const dark = Math.min(l1, l2);
    return (light + 0.05) / (dark + 0.05);
  };
  const overflowPx = Math.max(0, doc.scrollWidth - doc.clientWidth);
  const wideElements = Array.from(document.querySelectorAll('body *')).filter((el) => {
    const box = rect(el);
    if (!box.width || box.right <= vw + 1) return false;
    for(let parent=el.parentElement;parent;parent=parent.parentElement){
      const s=style(parent),r=rect(parent);
      if(/^(auto|scroll)$/.test(s.overflowX)&&parent.scrollWidth>parent.clientWidth&&r.left>=0&&r.right<=vw+1)return false;
    }
    const position = style(el).position;
    return position !== 'fixed' && position !== 'sticky';
  });
  const interactive = Array.from(document.querySelectorAll('a[href],button,input,select,textarea,[role="button"]'));
  const smallTargets = interactive.filter((el) => {
    const box = rect(el);
    return box.width > 0 && box.height > 0 && (box.height < 24 || box.width < 24);
  });
  const images = Array.from(document.images);
  const inputs = Array.from(document.querySelectorAll('input,select,textarea')).filter((el) =>
    !['hidden', 'submit', 'button', 'reset', 'image'].includes((el.type || '').toLowerCase()));
  const unlabeled = inputs.filter((el) => {
    if (el.labels && el.labels.length) return false;
    return !(el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.title);
  });
  const textNodes = Array.from(document.querySelectorAll('body *')).filter((el) => {
    if (!visible(el)) return false;
    if (!el.textContent || !el.textContent.trim()) return false;
    const hasElementChild = Array.from(el.childNodes).some((n) => n.nodeType === 1);
    return !hasElementChild;
  });
  let lowContrast = [];
  let tinyText = [];
  for (const el of textNodes.slice(0, 400)) {
    const computed = style(el);
    const size = parseFloat(computed.fontSize);
    const fg = parseColor(computed.color);
    if (fg && size) {
      const bold = parseInt(computed.fontWeight, 10) >= 700;
      const large = size >= 24 || (bold && size >= 18.66);
      const ratio = contrast(fg, background(el));
      if (ratio < (large ? 3 : 4.5) && lowContrast.length < 12) {
        lowContrast.push({ tag: el.tagName.toLowerCase(), ratio: Math.round(ratio * 100) / 100 });
      }
    }
    if (size && size < 12 && tinyText.length < 20) tinyText.push(el.tagName.toLowerCase());
  }
  const ids = Array.from(document.querySelectorAll('[id]')).map((el) => el.id);
  const duplicates = Array.from(new Set(ids.filter((id, index) => ids.indexOf(id) !== index)));
  const splitLabels=[];
  const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  let textNode,scanned=0;
  while((textNode=walker.nextNode())&&scanned++<1500){
    const el=textNode.parentElement,word=textNode.textContent.trim();
    if(!el||!visible(el)||!el.closest('nav,button,label,th,.bar,[data-chart-label]')||!/^\p{L}{3,24}$/u.test(word))continue;
    const range=document.createRange();range.selectNodeContents(textNode);
    const lines=new Set(Array.from(range.getClientRects()).filter(r=>r.width>0&&r.height>0).map(r=>Math.round(r.top)));
    if(lines.size>1&&splitLabels.length<20)splitLabels.push({tag:el.tagName.toLowerCase(),lines:lines.size,characters:word.length});
  }
  return {
    viewportWidth: vw,
    horizontalOverflowPx: overflowPx,
    overflowElements: wideElements.slice(0, 8).map((el) => el.tagName.toLowerCase()),
    smallTapTargets: smallTargets.length,
    imagesMissingAlt: images.filter((img) => !img.hasAttribute('alt')).length,
    imagesBroken: images.filter((img) => img.complete && img.naturalWidth === 0).length,
    unlabeledInputs: unlabeled.length,
    lowContrast: lowContrast,
    tinyText: Array.from(new Set(tinyText)),
    duplicateIds: duplicates.slice(0, 8),
    splitLabels,
    h1Count: document.querySelectorAll('h1').length,
    headingCount: document.querySelectorAll('h1').length,
    documentHeight: Math.max(doc.scrollHeight, document.body.scrollHeight),
    viewportHeight: window.innerHeight,
    requiredRegions: (options.requiredSelectors || []).map((selector) => {
      let elements;try { elements=Array.from(document.querySelectorAll(selector)); }
      catch { return {selector,count:0,insideFrame:false,error:'invalid_selector'}; }
      const inside=elements.map(el=>{
        const box=rect(el);
        if(!visible(el)||box.left<-.5||box.top<-.5||box.right>vw+.5||box.bottom>innerHeight+.5)return false;
        // Ancestor clipping can hide a region even while its own DOM box fits the frame.
        for(let parent=el.parentElement;parent;parent=parent.parentElement){
          const s=style(parent),r=rect(parent);
          if(/^(hidden|clip|auto|scroll)$/.test(s.overflowX)&&(box.left<r.left-.5||box.right>r.right+.5))return false;
          if(/^(hidden|clip|auto|scroll)$/.test(s.overflowY)&&(box.top<r.top-.5||box.bottom>r.bottom+.5))return false;
        }
        return true;
      });
      return {selector,count:elements.length,insideFrame:elements.length>0&&inside.every(Boolean)};
    }),
    hasViewportMeta: !!document.querySelector('meta[name="viewport"]'),
    htmlLang: document.documentElement.getAttribute('lang') || '',
    title: document.title || '',
  };
};

function checksFor(device, audit) {
  const rows = [];
  const add = (id, ok, detail) => rows.push({ id, ok: !!ok, detail: detail || null });
  add('no-horizontal-overflow', audit.horizontalOverflowPx <= 1,
    audit.horizontalOverflowPx > 1 ? audit.horizontalOverflowPx + 'px overflow' : null);
  add('no-widget-overflow', audit.overflowElements.length === 0,
    audit.overflowElements.join(', ') || null);
  add('viewport-meta', audit.hasViewportMeta, null);
  add('html-lang', !!audit.htmlLang, audit.htmlLang || null);
  add('document-title', audit.title.trim().length > 0, audit.title);
  add('single-h1', audit.h1Count === 1, 'h1 count=' + audit.h1Count);
  add('images-have-alt', audit.imagesMissingAlt === 0,
    audit.imagesMissingAlt ? audit.imagesMissingAlt + ' images missing alt' : null);
  add('images-loadable', audit.imagesBroken === 0,
    audit.imagesBroken ? audit.imagesBroken + ' broken images' : null);
  add('inputs-labeled', audit.unlabeledInputs === 0,
    audit.unlabeledInputs ? audit.unlabeledInputs + ' unlabeled inputs' : null);
  add('no-duplicate-ids', audit.duplicateIds.length === 0, audit.duplicateIds.join(', ') || null);
  add('text-contrast', audit.lowContrast.length === 0,
    audit.lowContrast.length ? JSON.stringify(audit.lowContrast.slice(0, 5)) : null);
  add('readable-text-size', audit.tinyText.length === 0, audit.tinyText.join(', ') || null);
  add('no-split-ui-labels', (audit.splitLabels || []).length===0,
    audit.splitLabels?.length ? 'wrapped single-word control/chart labels='+audit.splitLabels.length : null);
  if (device.captureFrame) {
    if (device.fit) add('frame-content-fits',audit.documentHeight<=device.height+1,
      'document='+audit.documentHeight+'px frame='+device.height+'px');
    for(const region of audit.requiredRegions || []) add('frame-region:'+region.selector,
      region.insideFrame,'matched='+region.count);
  }
  if (device.mobile) {
    add('tap-target>=24px', audit.smallTapTargets === 0,
      audit.smallTapTargets ? audit.smallTapTargets + ' targets under 24px' : null);
  }
  return rows;
}

async function main(argv) {
  const args = argumentsFor(argv);
  const devices = framesFor(args['--frames']);
  const target = await validateURL(args['--url'], true);
  const allowLocal = isLocal(target.hostname);
  const out = safeOut(args['--out']);
  const playwright = loadPlaywright();
  fs.mkdirSync(out, { recursive: true });
  const report = {
    url: reportURL(target.href),
    engine: 'harmonika-qa-matrix',
    mode: 'emulated-devices-only',
    devices: [],
    limitations: [
      'Emulated Chromium only; no physical devices, Safari or Firefox were exercised.',
      'Automated checks cover layout and common a11y signals, not full WCAG conformance.',
      'No credentials are submitted; authenticated areas require a supplied session.',
      ...(browserConfig().captureMethod==='pdf-raster'?['Images are Chromium screen-media PDF raster exports, not compositor screenshots. Inspect pixels; print fragmentation and dynamic surfaces may differ.']:[]),
    ],
  };
  let browser;
  try {
    browser = await playwright.chromium.launch(browserOptions());
    report.browserVersion=browser.version();
    for (const device of devices) {
      const context = await browser.newContext({
        viewport: { width: device.width, height: device.height },
        // Fixed raster frames must not auto-shrink an over-wide mobile layout.
        isMobile: device.captureFrame ? false : device.mobile,
        hasTouch: device.mobile,
        deviceScaleFactor: device.captureFrame ? 1 : device.mobile ? 2 : 1,
        locale: device.locale,
        colorScheme: device.scheme,
        serviceWorkers: 'block',
      });
      const record = { name: device.name, width: device.width, height: device.height,
        locale: device.locale, colorScheme: device.scheme, consoleErrors: [], requestFailures: [] };
      await context.route('**/*', async (route) => {
        try {
          const target = route.request().url();
          if (target.startsWith('data:') || target.startsWith('blob:') || target.startsWith('about:')) {
            return route.continue();
          }
          await validateURL(target, allowLocal);
          return route.continue();
        } catch (error) {
          record.requestFailures.push({ url: reportURL(route.request().url()), error: 'request_blocked' });
          return route.abort('blockedbyclient');
        }
      });
      const page = await context.newPage();
      page.on('requestfailed', request => record.requestFailures.push({url:reportURL(request.url()),error:'request_failed'}));
      page.on('console', (msg) => { if (msg.type() === 'error') record.consoleErrors.push(msg.text().slice(0, 300)); });
      page.on('pageerror', (error) => record.consoleErrors.push('pageerror: ' + error.message.slice(0, 300)));
      try {
        const response = await page.goto(target.href, { waitUntil: 'networkidle', timeout: 30000 });
        await page.evaluate(() => document.fonts.ready);
        record.status = response ? response.status() : null;
        const audit = await page.evaluate(PAGE_AUDIT,{requiredSelectors:device.requiredSelectors || []});
        record.audit = audit;
        record.checks = checksFor(device, audit);
        const shot = path.join(out, 'shot-' + device.name + '.png');
        record.captureMethod=await capturePNG(page,device,audit,shot);
        record.screenshot = shot;
        const png=fs.readFileSync(shot);
        record.image={width:png.readUInt32BE(16),height:png.readUInt32BE(20)};
        if(device.captureFrame)record.checks.push({id:'capture-pixels-exact',ok:record.image.width===device.width&&record.image.height===device.height});
      } catch (error) {
        record.error = error.message.slice(0, 300);
        if(record.checks)record.checks.push({id:'capture-export',ok:false,detail:record.error});
        else record.checks = [{ id: 'page-load', ok: false, detail: record.error }];
      }
      await context.close();
      record.checks.push({id:'http-success',ok:record.status>=200&&record.status<400});
      record.checks.push({id:'no-browser-errors',ok:record.consoleErrors.length===0});
      record.checks.push({id:'no-request-failures',ok:record.requestFailures.length===0});
      record.failed = record.checks.filter((check) => !check.ok).map((check) => check.id);
      report.devices.push(record);
    }
  } finally {
    if (browser) await browser.close();
  }
  const totals = {};
  for (const device of report.devices) {
    for (const check of device.checks || []) {
      totals[check.id] = totals[check.id] || { pass: 0, fail: 0 };
      if (check.ok) totals[check.id].pass += 1; else totals[check.id].fail += 1;
    }
  }
  report.checkTotals = totals;
  report.devicesWithFailures = report.devices.filter((d) => d.failed && d.failed.length).map((d) => d.name);
  report.verdict = report.devicesWithFailures.length === 0 ? 'pass' : 'needs-work';
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify({
    ok: true,
    report: path.join(out, 'report.json'),
    verdict: report.verdict,
    devices: report.devices.length,
    devicesWithFailures: report.devicesWithFailures,
    checkTotals: totals,
    captureMethods:Array.from(new Set(report.devices.map(d=>d.captureMethod).filter(Boolean))),
  }));
}

module.exports={MATRIX,framesFor,checksFor,PAGE_AUDIT,argumentsFor,validateURL,safeOut,browserOptions};
if(require.main===module)main(process.argv.slice(2)).catch((error) => {
  process.stderr.write(JSON.stringify({ ok: false, error: error.message.slice(0, 300) }));
  process.exitCode = 1;
});
