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

const MATRIX = [
  { name: 'desktop-1440', width: 1440, height: 900, mobile: false, locale: 'en-US', scheme: 'light' },
  { name: 'desktop-dark-1280', width: 1280, height: 800, mobile: false, locale: 'id-ID', scheme: 'dark' },
  { name: 'tablet-834', width: 834, height: 1112, mobile: true, locale: 'en-US', scheme: 'light' },
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
    if (!['--url', '--out'].includes(key) || !argv[i + 1] || result[key]) {
      throw new Error('Usage: qa_matrix.cjs --url URL --out ABSOLUTE_EMPTY_DIRECTORY');
    }
    result[key] = argv[i + 1];
  }
  if (!result['--url'] || !result['--out']) throw new Error('Both --url and --out are required');
  return result;
}

function loadPlaywright() {
  const override = process.env.HARMONIKA_PLAYWRIGHT_MODULE;
  if (override && !path.isAbsolute(override)) {
    throw new Error('HARMONIKA_PLAYWRIGHT_MODULE must be an absolute trusted module path');
  }
  const candidates = [override, 'playwright',
    path.join(os.homedir(), '.config/harmonika-terminal/browser-runtime/node_modules/playwright')]
    .filter(Boolean);
  for (const candidate of candidates) {
    try {
      const module = require(candidate);
      if (module.chromium && typeof module.chromium.launch === 'function') return module;
    } catch (error) { /* try next candidate */ }
  }
  throw new Error('Unsupported: Playwright runtime missing. Install it explicitly in the project.');
}

const PAGE_AUDIT = () => {
  const doc = document.scrollingElement || document.documentElement;
  const vw = window.innerWidth;
  const style = (el) => getComputedStyle(el);
  const rect = (el) => el.getBoundingClientRect();
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
    headingCount: document.querySelectorAll('h1').length,
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
  if (device.mobile) {
    add('tap-target>=24px', audit.smallTapTargets === 0,
      audit.smallTapTargets ? audit.smallTapTargets + ' targets under 24px' : null);
  }
  return rows;
}

async function main(argv) {
  const args = argumentsFor(argv);
  const target = await validateURL(args['--url'], true);
  const allowLocal = isLocal(target.hostname);
  const out = safeOut(args['--out']);
  const playwright = loadPlaywright();
  fs.mkdirSync(out, { recursive: true });
  const report = {
    url: target.href,
    engine: 'harmonika-qa-matrix',
    mode: 'emulated-devices-only',
    devices: [],
    limitations: [
      'Emulated Chromium only; no physical devices, Safari or Firefox were exercised.',
      'Automated checks cover layout and common a11y signals, not full WCAG conformance.',
      'No credentials are submitted; authenticated areas require a supplied session.',
    ],
  };
  let browser;
  try {
    browser = await playwright.chromium.launch({ headless: true });
    for (const device of MATRIX) {
      const context = await browser.newContext({
        viewport: { width: device.width, height: device.height },
        isMobile: device.mobile,
        hasTouch: device.mobile,
        deviceScaleFactor: device.mobile ? 2 : 1,
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
          record.requestFailures.push({ url: route.request().url(), error: error.message });
          return route.abort('blockedbyclient');
        }
      });
      const page = await context.newPage();
      page.on('console', (msg) => { if (msg.type() === 'error') record.consoleErrors.push(msg.text().slice(0, 300)); });
      page.on('pageerror', (error) => record.consoleErrors.push('pageerror: ' + error.message.slice(0, 300)));
      try {
        const response = await page.goto(target.href, { waitUntil: 'networkidle', timeout: 30000 });
        record.status = response ? response.status() : null;
        const audit = await page.evaluate(PAGE_AUDIT);
        record.audit = audit;
        record.checks = checksFor(device, audit);
        const shot = path.join(out, 'shot-' + device.name + '.png');
        await page.screenshot({ path: shot, fullPage: true });
        record.screenshot = shot;
      } catch (error) {
        record.error = error.message.slice(0, 300);
        record.checks = [{ id: 'page-load', ok: false, detail: record.error }];
      }
      await context.close();
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
  }));
}

main(process.argv.slice(2)).catch((error) => {
  process.stderr.write(JSON.stringify({ ok: false, error: error.message.slice(0, 300) }));
  process.exitCode = 1;
});
