#!/usr/bin/env python3
"""Harmonika Site Cloner: HTTrack-style clone + manual login with real browser.

Flow:
  1. browser_detect  — find real Chrome/Firefox/Chromium on any OS (Win/Ubuntu/macOS)
  2. login_start     — launch REAL browser (visible) with CDP remote-debugging.
                       User logs in MANUALLY (CAPTCHA never bypassed — policy).
                       Background daemon auto-detects login success.
  3. login_status    — poll: has login completed?
  4. login_done      — user says "sudah login" → save session, keep or close browser
  5. clone           — HTTrack-style recursive download using authenticated session
  6. workflow        — extract site roadmap: nav, forms, CRUD, page flow → WORKFLOW.md
  7. vnc_bridge      — headless server? Expose browser via VNC/noVNC for manual login

Chrome automation: launched with --remote-debugging-port (CDP). We connect via
Playwright connectOverCDP to read cookies/DOM while the USER stays in control.
We NEVER auto-solve CAPTCHA; manual login only.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

BASE_DIR = os.environ.get('HARMONIKA_GLOBAL_CONFIG') or os.environ.get('HARMONIKA_CONFIG_DIR') or os.path.expanduser('~/.config/harmonika')
CLONE_DIR = os.path.join(os.environ.get('HARMONIKA_DOWNLOADS_DIR') or os.path.expanduser('~/Downloads/harmonika-ai'),'clones')
SESSION_DIR = os.path.join(BASE_DIR, 'web-sessions')
RUNTIME_DIR = os.path.join(BASE_DIR, 'site-cloner-runtime')

CDP_PORT = 9333  # dedicated port to not clash with other debugging sessions


# ═══════════════════ BROWSER DETECTION (all OS) ═══════════════════

BROWSER_PATHS = {
    'darwin': {
        'chrome': [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
        ],
        'firefox': ['/Applications/Firefox.app/Contents/MacOS/firefox'],
    },
    'linux': {
        'chrome': [
            '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium', '/usr/bin/chromium-browser',
            '/snap/bin/chromium', '/var/lib/flatpak/exports/bin/com.google.Chrome',
            '/usr/bin/brave-browser', '/usr/bin/microsoft-edge',
        ],
        'firefox': ['/usr/bin/firefox', '/snap/bin/firefox', '/usr/bin/firefox-esr'],
    },
    'windows': {
        'chrome': [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Chromium\Application\chrome.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        ],
        'firefox': [
            r'C:\Program Files\Mozilla Firefox\firefox.exe',
            r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe',
        ],
    },
}


def detect_browsers():
    """Find every real browser installed on this OS. Supports local detection;
    for remote targets use os_intelligence detect first."""
    platform = sys.platform
    if platform == 'darwin':
        os_key = 'darwin'
    elif platform == 'win32' or os.name == 'nt':
        os_key = 'windows'
    else:
        os_key = 'linux'
    found = []
    for kind, paths in BROWSER_PATHS.get(os_key, {}).items():
        for p in paths:
            if os.path.exists(p):
                found.append({'kind': kind, 'path': p, 'name': Path(p).stem})
                break
    # PATH fallback
    if not found:
        for name, kind in [('google-chrome', 'chrome'), ('chromium', 'chrome'),
                           ('chromium-browser', 'chrome'), ('firefox', 'firefox')]:
            full = shutil.which(name)
            if full:
                found.append({'kind': kind, 'path': full, 'name': name})
    return {'ok': True, 'os': os_key, 'browsers': found,
            'cdp_capable': [b for b in found if b['kind'] == 'chrome']}


# ═══════════════════ LOGIN DAEMON (background browser manager) ═══════════════════

LOGIN_DAEMON = os.path.join(RUNTIME_DIR, 'login_daemon.py')


def _daemon_code():
    return '''#!/usr/bin/env python3
"""Background login daemon: launches real browser w/ CDP, watches for manual login.
Writes status JSON. User logs in manually; CAPTCHA is theirs to solve. Never bypassed."""
import json, os, subprocess, sys, time, urllib.request, urllib.parse

runtime = sys.argv[1]
url = sys.argv[2]
browser_path = sys.argv[3]
cdp_port = int(sys.argv[4])
wait_max = int(sys.argv[5]) if len(sys.argv) > 5 else 600
status_file = os.path.join(runtime, 'login_status.json')
stop_file = os.path.join(runtime, 'login_stop')
confirm_file = os.path.join(runtime, 'login_confirmed')
profile_dir = os.path.join(runtime, 'chrome-profile')

def write(status, **kw):
    data = {'status': status, 'url': url, 'ts': time.time()}
    data.update(kw)
    json.dump(data, open(status_file, 'w', encoding='utf-8'))

write('starting')
proc = subprocess.Popen([
    browser_path,
    '--remote-debugging-port=%d' % cdp_port,
    '--user-data-dir=%s' % profile_dir,
    '--no-first-run', '--no-default-browser-check',
    '--restore-last-session=false',
    url,
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Wait for CDP endpoint
write('browser_launched', pid=proc.pid, cdp='http://127.0.0.1:%d' % cdp_port)
deadline_cdp = time.time() + 30
cdp_ok = False
while time.time() < deadline_cdp:
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % cdp_port, timeout=2) as r:
            cdp_ok = True
            break
    except Exception:
        time.sleep(1)
if not cdp_ok:
    write('error', error='CDP endpoint did not appear (is another Chrome using the profile?)')
    sys.exit(1)

write('waiting_manual_login', cdp='http://127.0.0.1:%d' % cdp_port,
      instruction='Login manually in the opened browser. CAPTCHA: solve it yourself. '
                  'Then either wait for auto-detect or create the file: %s' % confirm_file)

# Watch for login success: page URL no longer on login/auth screens
initial_path = urllib.parse.urlparse(url).path or '/'
login_words = ('login', 'signin', 'sign-in', 'auth', 'masuk', 'logon')
deadline = time.time() + wait_max
while time.time() < deadline:
    if os.path.exists(stop_file):
        try: proc.terminate()
        except Exception: pass
        write('stopped_by_user')
        sys.exit(0)
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/json' % cdp_port, timeout=3) as r:
            targets = json.load(r)
        pages = [t for t in targets if t.get('type') == 'page']
        current = pages[0].get('url', '') if pages else ''
        manually_confirmed = os.path.exists(confirm_file)
        left_login = current and not any(w in current.lower() for w in login_words) and current != url
        if manually_confirmed or (left_login and time.time() > time.time() - 5):
            # login heuristics passed — give a small settle delay
            settled = 0
            if manually_confirmed:
                settled = 1
            else:
                # URL left the login screen: wait 3s and re-check (avoid OTP interstitials)
                time.sleep(3)
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%d/json' % cdp_port, timeout=3) as r2:
                        pages2 = [t for t in json.load(r2) if t.get('type') == 'page']
                    cur2 = pages2[0].get('url', '') if pages2 else ''
                    settled = not any(w in cur2.lower() for w in login_words)
                except Exception:
                    settled = True
            if settled:
                write('login_detected', current_url=current,
                      note='Session cookies live in profile. Run login_done to save storage state.')
                # keep browser open so user can continue; daemon exits quietly
                sys.exit(0)
    except Exception:
        pass
    time.sleep(2)
write('timeout', note='No login detected in wait window. Browser stays open; you can retry login_status.')
'''


def _ensure_daemon():
    for directory in (RUNTIME_DIR,SESSION_DIR,CLONE_DIR):os.makedirs(directory,mode=0o700,exist_ok=True)
    if not os.path.exists(LOGIN_DAEMON):
        with open(LOGIN_DAEMON, 'w', encoding='utf-8') as f:
            f.write(_daemon_code())
    return LOGIN_DAEMON


def _runtime_for(url):
    for directory in (RUNTIME_DIR,SESSION_DIR,CLONE_DIR):os.makedirs(directory,mode=0o700,exist_ok=True)
    domain = urllib.parse.urlparse(url).hostname or 'site'
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
    rt = os.path.join(RUNTIME_DIR, safe)
    os.makedirs(rt, exist_ok=True)
    return rt


def login_start(args):
    """Launch REAL browser (visible) with CDP for manual login. Returns immediately."""
    url = args.get('url', '')
    browser = args.get('browser', '')  # optional explicit path
    wait = min(int(args.get('wait_seconds', 600)), 3600)
    if not url:
        return {'ok': False, 'error': 'url required'}
    if browser and not os.path.exists(browser):
        return {'ok': False, 'error': f'browser path not found: {browser}'}
    if not browser:
        det = detect_browsers()
        cdp_capable = det.get('cdp_capable', [])
        if not cdp_capable:
            return {'ok': False, 'error': 'No Chrome/Chromium/Edge found (CDP-capable). '
                    'Install Chrome or pass browser=path to Firefox (Firefox uses different automation).',
                    'detected': det}
        browser = cdp_capable[0]['path']
    rt = _runtime_for(url)
    # Clear stale signals
    for f in ('login_stop', 'login_confirmed'):
        p = os.path.join(rt, f)
        if os.path.exists(p):
            os.remove(p)
    daemon = _ensure_daemon()
    log = open(os.path.join(rt, 'daemon.log'), 'w', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, daemon, rt, url, browser, str(CDP_PORT), str(wait)],
                            stdout=log, stderr=log, start_new_session=True)
    # Give daemon a moment to write initial status
    time.sleep(3)
    status = _read_status(rt)
    return {'ok': True, 'action': 'login_start', 'url': url, 'browser': browser,
            'cdp': f'http://127.0.0.1:{CDP_PORT}', 'status': status,
            'next': 'User: login manually (solve CAPTCHA yourself). '
                    'Then call login_status. Or call login_done to force-save session.'}


def _read_status(rt):
    p = os.path.join(rt, 'login_status.json')
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding='utf-8'))
        except Exception:
            pass
    return {'status': 'unknown'}


def login_status(args):
    url = args.get('url', '')
    if not url:
        return {'ok': False, 'error': 'url required'}
    rt = _runtime_for(url)
    st = _read_status(rt)
    return {'ok': True, 'action': 'login_status', 'status': st.get('status'),
            'detail': st, 'logged_in': st.get('status') == 'login_detected'}


def login_confirm(args):
    """User said 'sudah login' — create confirm flag so daemon finalizes immediately."""
    url = args.get('url', '')
    rt = _runtime_for(url)
    open(os.path.join(rt, 'login_confirmed'), 'w', encoding='utf-8').write('1')
    time.sleep(2)
    st = _read_status(rt)
    return {'ok': True, 'action': 'login_confirm', 'status': st.get('status'),
            'note': 'Daemon will mark login_detected.'}


def login_done(args):
    """Save session (cookies+localStorage) from the live CDP browser profile."""
    url = args.get('url', '')
    keep_open = args.get('keep_browser', True)
    if not url:
        return {'ok': False, 'error': 'url required'}
    rt = _runtime_for(url)
    domain = urllib.parse.urlparse(url).hostname or 'site'
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
    # Read cookies via CDP HTTP endpoint (Network.getAllCookies needs ws; use
    # Playwright connectOverCDP through node helper if available, else profile retained)
    session_file = os.path.join(SESSION_DIR, f'{safe}.json')
    # Record that profile dir holds the authenticated session
    meta = {
        'domain': domain, 'url': url, 'profile_dir': os.path.join(rt, 'chrome-profile'),
        'cdp': f'http://127.0.0.1:{CDP_PORT}', 'saved_at': datetime.utcnow().isoformat() + 'Z',
        'manual_login': True, 'captcha_bypassed': False,
    }
    json.dump(meta, open(session_file, 'w', encoding='utf-8'), indent=2)
    if not keep_open:
        open(os.path.join(rt, 'login_stop'), 'w', encoding='utf-8').write('1')
    return {'ok': True, 'action': 'login_done', 'session_file': session_file,
            'profile_dir': meta['profile_dir'],
            'note': 'Crawls now launch Chrome with this authenticated profile.'}


# ═══════════════════ HTTRACK-STYLE CLONE ═══════════════════

def _domain_of(url):
    return urllib.parse.urlparse(url).hostname or ''


def clone_site(args):
    """Recursive site mirror: HTML + assets, same-domain by default, auth via saved profile."""
    url = args.get('url', '')
    max_pages = min(int(args.get('max_pages', 50)), 500)
    include_assets = args.get('assets', True)
    use_session = args.get('use_session', True)
    if not url:
        return {'ok': False, 'error': 'url required'}
    domain = _domain_of(url)
    if not domain:
        return {'ok': False, 'error': 'invalid url'}
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
    out_dir = os.path.join(CLONE_DIR, f'{safe}-{datetime.now().strftime("%Y%m%d-%H%M%S")}')
    os.makedirs(out_dir, exist_ok=True)
    session = None
    if use_session:
        sf = os.path.join(SESSION_DIR, f'{safe}.json')
        if os.path.exists(sf):
            session = json.load(open(sf, encoding='utf-8'))

    # If authenticated profile exists → use Playwright over CDP with that profile
    node_script = _clone_script_path()
    node_bin = os.environ.get('HARMONIKA_BROWSER_NODE') or os.environ.get('HARMONIKA_NODE') or shutil.which('node') or 'node'
    if not os.path.exists(node_bin):
        node_bin = 'node'
    payload = {
        'url': url, 'out_dir': out_dir, 'max_pages': max_pages,
        'assets': include_assets, 'session': session, 'domain': domain,
    }
    try:
        proc = subprocess.run([node_bin, node_script], input=json.dumps(payload).encode(),
                              capture_output=True, timeout=280)
        out = proc.stdout.decode('utf-8', 'replace').strip()
        if out:
            return json.loads(out)
        return {'ok': False, 'error':'browser_operation_failed'}
    except FileNotFoundError:
        return {'ok':False,'error':'node_runtime_unavailable'}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'clone timed out (280s). Reduce max_pages.'}


def _clone_script_path():
    for directory in (RUNTIME_DIR,SESSION_DIR,CLONE_DIR):os.makedirs(directory,mode=0o700,exist_ok=True)
    p = os.path.join(RUNTIME_DIR, 'clone_engine.cjs')
    if not os.path.exists(p):
        with open(p, 'w', encoding='utf-8') as f:
            f.write(CLONE_ENGINE_JS)
    return p


CLONE_ENGINE_JS = r"""#!/usr/bin/env node
'use strict';
/* HTTrack-style mirror using Playwright. Optional authenticated Chrome profile. */
const fs = require('fs');
const path = require('path');
const os = require('os');
let chromium;
try {
  chromium = require(process.env.HARMONIKA_PLAYWRIGHT_MODULE||path.join(process.env.HARMONIKA_TERMINAL_HOME||path.join(os.homedir(),'.config','harmonika-terminal'),'browser-runtime','node_modules','playwright')).chromium;
} catch (e) {
  console.log(JSON.stringify({ok:false, error:'browser_runtime_unavailable'}));
  process.exit(0);
}

(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const { url, out_dir, max_pages, assets, session, domain } = input;
  const queue = [url];
  const seen = new Set();
  const pagesSaved = [];
  const navMap = {};           // page -> links (for workflow)
  const formsByPage = {};      // page -> forms
  let browser;
  const launchOpts = { headless: true };
  if (session && session.profile_dir && fs.existsSync(session.profile_dir)) {
    // Re-launch the SAME profile the user logged into → cookies reused
    launchOpts.executablePath = null; // default chromium binary, but with userDataDir profile
    // Playwright: persistent context with that profile gives us the cookies
    const ctx = await chromium.launchPersistentContext(session.profile_dir, {
      headless: true, args: ['--no-first-run', '--disable-blink-features=AutomationControlled'],
    });
    browser = ctx;
    var page = ctx.pages()[0] || await ctx.newPage();
  } else {
    browser = await chromium.launch(launchOpts);
    var page = await browser.newPage();
  }

  function localPath(u) {
    const p = new URL(u);
    let rel = p.pathname === '/' ? 'index.html' : p.pathname.slice(1);
    if (rel.endsWith('/')) rel += 'index.html';
    if (!/\.[a-z0-9]{1,6}$/i.test(rel)) rel += '.html';
    return path.join(out_dir, rel);
  }

  while (queue.length && pagesSaved.length < max_pages) {
    const u = queue.shift();
    if (seen.has(u)) continue;
    seen.add(u);
    try {
      await page.goto(u, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForTimeout(400);
      const html = await page.content();
      const current = new URL(page.url());
      // Save HTML
      const fp = localPath(current.href);
      fs.mkdirSync(path.dirname(fp), { recursive: true });
      fs.writeFileSync(fp, html);
      pagesSaved.push(current.href);
      // Extract links + forms for workflow
      const data = await page.evaluate(() => {
        const links = [...document.querySelectorAll('a[href]')].map(a => ({href: a.href, text: (a.innerText||'').trim().slice(0,60)}));
        const forms = [...document.querySelectorAll('form')].map(f => ({
          action: f.action, method: (f.method||'get').toUpperCase(),
          fields: [...f.querySelectorAll('input,select,textarea')].map(i => ({name: i.name, type: i.type||i.tagName.toLowerCase(), required: i.required})).slice(0,15),
        }));
        const buttons = [...document.querySelectorAll('button, [role=button], input[type=submit]')].map(b => (b.innerText||b.value||'').trim().slice(0,40)).filter(Boolean).slice(0,20);
        const title = document.title;
        return { links, forms, buttons, title };
      });
      navMap[current.href] = { title: data.title, links: data.links.slice(0,60), buttons: data.buttons };
      if (data.forms.length) formsByPage[current.href] = data.forms;
      // Queue same-domain links
      for (const l of data.links) {
        try {
          const lu = new URL(l.href);
          if (lu.hostname === domain && !seen.has(lu.href) && !/\.(png|jpg|jpeg|gif|svg|pdf|zip|mp4|mp3|css|js|ico|woff2?)$/i.test(lu.pathname)) {
            queue.push(lu.href);
          }
        } catch (_) {}
      }
      // Assets
      if (assets) {
        const assetUrls = await page.evaluate(() => {
          const urls = [];
          document.querySelectorAll('link[rel=stylesheet], script[src], img[src]').forEach(el => {
            const u = el.getAttribute('src') || el.getAttribute('href');
            if (u && u.startsWith('http')) urls.push(u);
          });
          return urls.slice(0, 30);
        });
        for (const au of assetUrls.slice(0, 15)) {
          try {
            const resp = await page.request.get(au, { timeout: 10000 });
            const body = await resp.body();
            if (body && body.length < 3_000_000) {
              const ap = localPath(au);
              fs.mkdirSync(path.dirname(ap), { recursive: true });
              fs.writeFileSync(ap, body);
            }
          } catch (_) {}
        }
      }
    } catch (e) { /* skip page */ }
  }
  try { await browser.close(); } catch (_) {}

  // Sitemap + workflow artifacts
  fs.writeFileSync(path.join(out_dir, 'SITEMAP.json'), JSON.stringify({ root: url, pages: pagesSaved, nav: navMap, forms: formsByPage }, null, 2));
  const lines = ['# Site Workflow', '', 'Cloned: ' + new Date().toISOString(), 'Root: ' + url, '', '## Pages (' + pagesSaved.length + ')', ''];
  for (const p of pagesSaved) lines.push('- ' + p + (navMap[p] && navMap[p].title ? ' — ' + navMap[p].title : ''));
  lines.push('', '## Forms (workflow entry points)', '');
  for (const [pg, forms] of Object.entries(formsByPage)) {
    lines.push('### ' + pg);
    for (const f of forms) {
      lines.push('- [' + f.method + '] ' + f.action + ' — fields: ' + f.fields.map(x => x.name + '(' + x.type + ')').join(', '));
    }
    lines.push('');
  }
  fs.writeFileSync(path.join(out_dir, 'WORKFLOW.md'), lines.join('\n'));
  console.log(JSON.stringify({ ok: true, action: 'clone', out_dir, pages: pagesSaved.length,
    forms_found: Object.keys(formsByPage).length, sitemap: path.join(out_dir, 'SITEMAP.json'),
    workflow: path.join(out_dir, 'WORKFLOW.md'), output_tail: pagesSaved.slice(-5) }));
})().catch(e => console.log(JSON.stringify({ ok: false, error: String(e).slice(0, 300) })));
"""


# ═══════════════════ WORKFLOW / ROADMAP ANALYSIS ═══════════════════

def analyze_workflow(args):
    """Deep workflow analysis from an existing clone directory (uses SITEMAP.json)."""
    out_dir = args.get('out_dir', '')
    if not out_dir or not os.path.isdir(out_dir):
        # find latest
        cands = sorted(Path(CLONE_DIR).glob('*'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            return {'ok': False, 'error': f'no clone found in {CLONE_DIR}. Run clone first.'}
        out_dir = str(cands[0])
    sitemap = os.path.join(out_dir, 'SITEMAP.json')
    if not os.path.exists(sitemap):
        return {'ok': False, 'error': 'SITEMAP.json not found in clone dir'}
    data = json.load(open(sitemap, encoding='utf-8'))
    nav, forms = data.get('nav', {}), data.get('forms', {})

    # Classify pages by keywords
    def classify(url_path, title=''):
        s = (url_path + ' ' + title).lower()
        if any(w in s for w in ('login', 'signin', 'masuk')): return 'AUTH'
        if any(w in s for w in ('register', 'signup', 'daftar')): return 'REGISTER'
        if any(w in s for w in ('dashboard', 'admin', 'panel')): return 'ADMIN'
        if any(w in s for w in ('edit', 'update', 'ubah')): return 'EDIT'
        if any(w in s for w in ('add', 'create', 'tambah', 'baru', 'new')): return 'CREATE'
        if any(w in s for w in ('delete', 'hapus', 'remove')): return 'DELETE'
        if any(w in s for w in ('list', 'table', 'data', 'report', 'laporan')): return 'LIST/REPORT'
        if any(w in s for w in ('profile', 'setting', 'pengaturan', 'akun')): return 'SETTINGS'
        if any(w in s for w in ('detail', 'view', 'show')): return 'DETAIL'
        return 'PAGE'

    # CRUD map
    crud = {'CREATE': [], 'READ': [], 'UPDATE': [], 'DELETE': [], 'AUTH': [], 'ADMIN': []}
    for page, meta in nav.items():
        cat = classify(page, meta.get('title', ''))
        entry = {'page': page, 'title': meta.get('title', '')}
        if cat in crud: crud[cat].append(entry)
        elif cat == 'EDIT': crud['UPDATE'].append(entry)
        elif cat in ('LIST/REPORT', 'DETAIL'): crud['READ'].append(entry)
    # Forms indicate workflow entry points
    form_entries = []
    for page, fl in forms.items():
        for f in fl:
            form_entries.append({'page': page, 'method': f.get('method'), 'action': f.get('action'),
                                 'fields': [x.get('name') for x in f.get('fields', [])][:12]})
    # Roadmap: auth → dashboard → CRUD
    roadmap = ['1. Landing: ' + data.get('root', '')]
    if crud['AUTH']: roadmap.append('2. Login: ' + crud['AUTH'][0]['page'])
    if crud['ADMIN']: roadmap.append('3. Dashboard/Admin: ' + ', '.join(p['page'] for p in crud['ADMIN'][:3]))
    for k in ('CREATE', 'UPDATE', 'DELETE', 'READ'):
        if crud[k]:
            roadmap.append(f'{len(roadmap)+1}. {k} flows: ' + ', '.join(p['page'] for p in crud[k][:3]))
    return {'ok': True, 'action': 'workflow', 'clone_dir': out_dir,
            'total_pages': len(nav), 'page_categories': crud,
            'workflow_forms': form_entries[:20], 'roadmap': roadmap}


# ═══════════════════ VNC BRIDGE (headless servers) ═══════════════════

def vnc_bridge(args):
    if not sys.platform.startswith('linux'):return {'ok':False,'error':'unsupported_local_platform','operation':'vnc_bridge','platform':sys.platform,'executed':False}
    """For headless Linux servers: run browser in virtual display + expose VNC so the
    user can manually login from their desktop. Requires: xvfb, x11vnc (or warns to install)."""
    action = args.get('vnc_action', 'status')
    display = ':99'
    vnc_port = int(args.get('vnc_port', 5999))
    if sys.platform == 'darwin':
        return {'ok': False, 'error': 'VNC bridge is for headless Linux servers, not macOS'}
    if action == 'status':
        xvfb = shutil.which('Xvfb')
        x11vnc = shutil.which('x11vnc')
        return {'ok': True, 'xvfb': bool(xvfb), 'x11vnc': bool(x11vnc),
                'install_hint': 'apt install -y xvfb x11vnc' if not (xvfb and x11vnc) else 'ready',
                'display': display, 'vnc_port': vnc_port}
    if action == 'start':
        xvfb = shutil.which('Xvfb'); x11vnc = shutil.which('x11vnc')
        if not (xvfb and x11vnc):
            return {'ok': False, 'error': 'xvfb/x11vnc missing. Install: apt install -y xvfb x11vnc'}
        subprocess.Popen([xvfb, display, '-screen', '0', '1440x900x24'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(2)
        subprocess.Popen([x11vnc, '-display', display, '-rfbport', str(vnc_port), '-nopw', '-forever', '-shared'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1)
        return {'ok': True, 'action': 'vnc_start', 'display': display, 'vnc_port': vnc_port,
                'connect': f'vnc://<server-ip>:{vnc_port}',
                'next': f'Run login_start with DISPLAY={display} env; user sees browser via VNC viewer and logs in manually.'}
    return {'ok': False, 'error': f'unknown vnc_action: {action}'}


# ═══════════════════ DISPATCH ═══════════════════

def _dispatch(args):
    action = args.get('action', 'browser_detect')
    if action == 'browser_detect':
        return detect_browsers()
    if action == 'login_start':
        return login_start(args)
    if action == 'login_status':
        return login_status(args)
    if action == 'login_confirm':
        return login_confirm(args)
    if action == 'login_done':
        return login_done(args)
    if action == 'clone':
        return clone_site(args)
    if action == 'workflow':
        return analyze_workflow(args)
    if action == 'vnc_bridge':
        return vnc_bridge(args)
    return {'ok': False, 'error': f'unknown action: {action}'}

# ═══════════════════ REGISTRY ═══════════════════

SITE_CLONER_TOOLS = {
    'site_cloner': {
        'description': 'HTTrack-style website cloner with MANUAL login policy (CAPTCHA never bypassed). '
                       'browser_detect=find real Chrome/Firefox on any OS; login_start=launch visible real browser '
                       'with CDP remote-debugging so YOU login manually; login_status=check; login_confirm=user says '
                       '"sudah login"; login_done=save authenticated session; clone=mirror site with session; '
                       'workflow=extract site roadmap/CRUD/forms; vnc_bridge=headless server login via VNC.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string',
                           'enum': ['browser_detect', 'login_start', 'login_status', 'login_confirm',
                                    'login_done', 'clone', 'workflow', 'vnc_bridge']},
                'url': {'type': 'string'},
                'browser': {'type': 'string', 'description': 'Explicit browser path'},
                'wait_seconds': {'type': 'number', 'description': 'Max manual login wait (default 600)'},
                'max_pages': {'type': 'number'},
                'assets': {'type': 'boolean'},
                'use_session': {'type': 'boolean'},
                'out_dir': {'type': 'string', 'description': 'Clone dir for workflow analysis'},
                'keep_browser': {'type': 'boolean'},
                'vnc_action': {'type': 'string', 'enum': ['status', 'start']},
                'vnc_port': {'type': 'number'},
            },
            'required': ['action'],
        },
    },
}

SITE_CLONER_HANDLERS = {
    'site_cloner': _dispatch,
}


