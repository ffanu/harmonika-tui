#!/usr/bin/env python3
"""Harmonika API Intelligence: read API keys/tokens, discover endpoints, understand features.

Actions:
  key_analyze    — Detect API key type (JWT/AWS/Google/Stripe/GitHub/Slack/OpenAI/generic),
                   decode JWT payload (exp/iss/scopes), format validation.
  discover       — Scan a website: extract API endpoints from page source + JS bundles +
                   localStorage/cookies + window config objects. Detect keys/tokens in use.
  docs_parse     — Parse API documentation: OpenAPI/Swagger JSON-YAML, or normal docs page
                   → full endpoint map (method, path, params, auth, description).
  probe          — Given base URL (+optional key): test common API paths, auth requirements,
                   which endpoints are public vs protected, response format.
  monitor_start  — Launch visible browser (CDP) and record ALL API traffic while you browse:
                   URLs, methods, auth headers, tokens. Like DevTools Network auto-log.
  monitor_status — Get captured API calls so far.
  monitor_stop   — Stop capture, save report.

Ethical scope: analyze APIs on sites YOU operate or have accounts on (like DevTools).
JWT decode is informational only (no signature verification).
"""
import base64
import json
import os
import re
import shutil
import uuid
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from pathlib import Path

BASE_DIR = os.environ.get('HARMONIKA_GLOBAL_CONFIG') or os.environ.get('HARMONIKA_CONFIG_DIR') or os.path.expanduser('~/.config/harmonika')
RUNTIME_DIR = os.path.join(BASE_DIR, 'api-intel-runtime')
REPORT_DIR = os.path.join(os.environ.get('HARMONIKA_DOWNLOADS_DIR') or os.path.expanduser('~/Downloads/harmonika-ai'),'api-intel')
def _ensure_state():
    os.makedirs(RUNTIME_DIR, mode=0o700, exist_ok=True)
    os.makedirs(REPORT_DIR, mode=0o700, exist_ok=True)

def _private_json(file,value):
    temporary=file+'.tmp-'+uuid.uuid4().hex
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(value,stream)
        os.replace(temporary,file)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)

def _safe_url(value):
    parsed=urllib.parse.urlsplit(value)
    if parsed.scheme not in ('http','https') or not parsed.hostname:raise ValueError('invalid_url')
    host=parsed.hostname+(':'+str(parsed.port) if parsed.port else '')
    return urllib.parse.urlunsplit((parsed.scheme,host,parsed.path,'',''))

# ═══════════════════ API KEY KNOWLEDGE BASE ═══════════════════

KEY_TYPES = [
    {'type': 'JWT', 'pattern': r'^eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*$',
     'desc': 'JSON Web Token — decodable payload (exp, sub, scopes)', 'decode': 'jwt'},
    {'type': 'AWS Access Key', 'pattern': r'^AKIA[0-9A-Z]{16}$',
     'desc': 'Amazon AWS access key ID (needs secret key pair)'},
    {'type': 'AWS Secret', 'pattern': r'^[0-9a-fA-F]{40}$', 'min_len': 40, 'max_len': 40,
     'desc': 'Possibly AWS secret access key (40 hex chars)'},
    {'type': 'Google API', 'pattern': r'^AIza[0-9A-Za-z\-_]{35}$',
     'desc': 'Google API key (Maps, Firebase, GCP services)'},
    {'type': 'Stripe Secret', 'pattern': r'^sk_(live|test)_[0-9a-zA-Z]{20,}$',
     'desc': 'Stripe SECRET key — sensitive, server-side only'},
    {'type': 'Stripe Publishable', 'pattern': r'^pk_(live|test)_[0-9a-zA-Z]{20,}$',
     'desc': 'Stripe publishable key — public, client-safe'},
    {'type': 'GitHub Token', 'pattern': r'^(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}$',
     'desc': 'GitHub personal/oauth/server token'},
    {'type': 'Slack Token', 'pattern': r'^xox[baprs]-[A-Za-z0-9-]{10,}$',
     'desc': 'Slack bot/user/app token'},
    {'type': 'OpenAI Key', 'pattern': r'^sk-[A-Za-z0-9]{20,}(T3BlbkFJ[A-Za-z0-9]{20,})?$',
     'desc': 'OpenAI API key'},
    {'type': 'Twilio SID', 'pattern': r'^SK[0-9a-fA-F]{32}$', 'desc': 'Twilio API key SID'},
    {'type': 'SendGrid', 'pattern': r'^SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}$',
     'desc': 'SendGrid API key'},
    {'type': 'Firebase', 'pattern': r'^[A-Za-z0-9_-]{22,}:[A-Za-z0-9_-]{100,}$',
     'desc': 'Possibly Firebase service account / FCM server key'},
    {'type': 'Shopify', 'pattern': r'^shpat_[0-9a-fA-F]{32}$', 'desc': 'Shopify admin API token'},
    {'type': 'Discord Bot', 'pattern': r'^[MN][A-Za-z\d_-]{23,}\.[A-Za-z\d_-]{6,}\.[A-Za-z\d_-]{27,}$',
     'desc': 'Discord bot token'},
    {'type': 'Telegram Bot', 'pattern': r'^\d{8,10}:[A-Za-z0-9_-]{35}$', 'desc': 'Telegram bot token'},
    {'type': 'Mapbox', 'pattern': r'^pk\.ey[A-Za-z0-9._-]+$', 'desc': 'Mapbox public token'},
    {'type': 'Anthropic Key', 'pattern': r'^sk-ant-[A-Za-z0-9_-]{20,}$', 'desc': 'Anthropic API key'},
    {'type': 'Bearer/Opaque', 'pattern': r'^[A-Za-z0-9\-_.~+/]+=*$', 'min_len': 20,
     'desc': 'Opaque bearer token (custom auth)'},
]

# Patterns for finding keys/tokens INSIDE page source & JS bundles
SOURCE_KEY_PATTERNS = [
    ('api_key_literal', re.compile(r'''["'](?:api[-_]?key|apikey|api[-_]?token|access[-_]?token|auth[-_]?token|client[-_]?secret|secret[-_]?key)["']\s*[:=]\s*["']([^"'\s]{12,200})["']''', re.I)),
    ('authorization_header', re.compile(r'''["']?authorization["']?\s*[:=]\s*["']?(?:Bearer|Basic|Token)?\s+([A-Za-z0-9\-_.~+/=]{20,})''', re.I)),
    ('jwt_inline', re.compile(r'(eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{5,})')),
    ('google_key', re.compile(r'\b(AIza[0-9A-Za-z\-_]{35})\b')),
    ('aws_key', re.compile(r'\b(AKIA[0-9A-Z]{16})\b')),
    ('stripe_pk', re.compile(r'\b(pk_(?:live|test)_[0-9a-zA-Z]{20,})\b')),
    ('stripe_sk', re.compile(r'\b(sk_(?:live|test)_[0-9a-zA-Z]{20,})\b')),
    ('github_pat', re.compile(r'\b(gh[pousr]_[A-Za-z0-9]{36,50})\b')),
    ('openai_key', re.compile(r'\b(sk-[A-Za-z0-9]{40,}T3BlbkFJ[A-Za-z0-9]{20,})\b')),
    ('bearer_assign', re.compile(r'''\b(?:token|auth|bearer|credential)[a-z_-]*\s*[:=]\s*["']([A-Za-z0-9\-_.]{24,})["']''', re.I)),
]

# Patterns for finding API endpoints in source/JS
ENDPOINT_PATTERNS = [
    ('fetch_call', re.compile(r'''fetch\(\s*["'`]([^"'`\s]{4,200})["'`]''')),
    ('axios_url', re.compile(r'''axios\s*.\s*(?:get|post|put|patch|delete)\s*\(\s*["'`]([^"'`\s]{4,200})["'`]''')),
    ('xhr_open', re.compile(r'''\.open\(\s*["'](GET|POST|PUT|DELETE|PATCH)["']\s*,\s*["'`]([^"'`\s]{4,200})["'`]''')),
    ('api_path', re.compile(r'''["'`](/api/[a-zA-Z0-9/_\-{}.]{2,150}|/v[1-3]/[a-zA-Z0-9/_\-{}.]{2,150}|/graphql|/rest/[a-zA-Z0-9/_\-{}.]{2,100})["'`]''')),
    ('base_url', re.compile(r'''(?:baseURL|baseUrl|BASE_URL|API_URL|apiUrl|endpoint)\s*[:=]\s*["'`](https?://[^"'`\s]{4,200}|/[^"'`\s]{2,100})["'`]''', re.I)),
    ('swagger_ref', re.compile(r'''["'`](/swagger\.json|/openapi\.json|/api-docs|/v[1-3]/api-docs|/swagger/v[1-3])["'`]''')),
]

COMMON_API_PATHS = [
    '/', '/api', '/api/v1', '/api/v2', '/v1', '/v2', '/v3',
    '/api/health', '/health', '/healthz', '/status', '/api/status',
    '/swagger.json', '/swagger/v1/swagger.json', '/openapi.json', '/api-docs',
    '/v3/api-docs', '/api/swagger.json', '/graphql', '/graphiql',
    '/api/docs', '/docs', '/api/info', '/.well-known/openid-configuration',
    '/rest', '/rpc', '/soap', '/ws', '/api/version', '/version',
    '/metrics', '/api/metrics', '/actuator', '/actuator/health',
]


# ═══════════════════ HELPERS ═══════════════════

def _fetch(url, timeout=15, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; HarmonikaIntel/1.0)', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace'), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')[:500], dict(e.headers)
    except Exception as e:
        return 0, str(e), {}


def _b64url_decode(s):
    s += '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s).decode('utf-8', 'replace')


def _mask(secret, show=6):
    s = str(secret)
    if len(s) <= show * 2:
        return s[:2] + '***'
    return f'{s[:show]}...{s[-4:]} (len={len(s)})'


# ═══════════════════ KEY ANALYZE ═══════════════════

def key_analyze(args):
    """Detect API key/token type, decode JWT, validate format."""
    key = args.get('key', '') or args.get('token', '')
    if not key:
        return {'ok': False, 'error': 'key or token required'}
    result = {'ok': True, 'action': 'key_analyze', 'masked': _mask(key), 'length': len(key)}

    # Detect type
    matched = None
    for kt in KEY_TYPES:
        if re.match(kt['pattern'], key):
            matched = kt
            break
    if matched:
        result['type'] = matched['type']
        result['description'] = matched['desc']
        result['format_valid'] = True
    else:
        result['type'] = 'Unknown'
        result['format_valid'] = False
        result['note'] = 'No known provider pattern matched. Custom/internal token?'

    # JWT decode
    if result['type'] == 'JWT' or (key.count('.') == 2 and key.startswith('eyJ')):
        try:
            header = json.loads(_b64url_decode(key.split('.')[0]))
            payload = json.loads(_b64url_decode(key.split('.')[1]))
            result['jwt'] = {
                'header': header,
                'payload': {k: v for k, v in payload.items() if k not in ('signature',)},
                'issuer': payload.get('iss'), 'subject': payload.get('sub'),
                'audience': payload.get('aud'),
                'scopes': payload.get('scope') or payload.get('scopes') or payload.get('permissions'),
                'issued_at': datetime.utcfromtimestamp(payload['iat']).isoformat() if payload.get('iat') else None,
                'expires_at': datetime.utcfromtimestamp(payload['exp']).isoformat() if payload.get('exp') else None,
                'expired': bool(payload.get('exp') and payload['exp'] < time.time()),
                'signature_present': bool(key.split('.')[2]) if len(key.split('.')) > 2 else False,
            }
            sensitive = ('password', 'secret', 'credential')
            result['jwt']['payload'] = {k: ('***' if any(s in k.lower() for s in sensitive) else v)
                                        for k, v in result['jwt']['payload'].items()}
        except Exception as e:
            result['jwt_error'] = f'decode failed: {e}'

    # Risk assessment
    risks = []
    if result['type'] in ('Stripe Secret', 'AWS Access Key', 'GitHub Token', 'OpenAI Key', 'Anthropic Key', 'Slack Token', 'Discord Bot', 'Telegram Bot', 'SendGrid', 'Shopify'):
        risks.append('HIGH-privilege key — never expose client-side; rotate if public')
    if result.get('jwt', {}).get('expired'):
        risks.append('JWT is EXPIRED — refresh needed')
    if result['type'] == 'Unknown' and len(key) > 20:
        risks.append('Opaque token — check provider docs for lifecycle/rotation')
    result['risk_notes'] = risks or ['No specific risk flags']
    result['safety'] = 'Do not commit to git. Store in env vars/secret manager.'
    return result


# ═══════════════════ DISCOVER (website scan via Playwright) ═══════════════════

def discover(args):
    """Scan website: endpoints + keys from source, JS bundles, localStorage, config."""
    url = args.get('url', '')
    if not url:
        return {'ok': False, 'error': 'url required'}
    node_script = _discover_script_path()
    node_bin = os.environ.get('HARMONIKA_BROWSER_NODE') or os.environ.get('HARMONIKA_NODE') or shutil.which('node') or 'node'
    if not os.path.exists(node_bin):
        node_bin = 'node'
    # Reuse site_cloner session profile if exists (scan behind login)
    domain = urllib.parse.urlparse(url).hostname or ''
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
    profile = os.path.join(BASE_DIR, 'site-cloner-runtime', safe, 'chrome-profile')
    payload = {'url': url, 'profile_dir': profile if os.path.exists(profile) else None}
    try:
        proc = subprocess.run([node_bin, node_script], input=json.dumps(payload).encode(),
                              capture_output=True, timeout=180)
        out = proc.stdout.decode('utf-8', 'replace').strip()
        if out:
            return json.loads(out)
        return {'ok': False, 'error':'browser_operation_failed'}
    except FileNotFoundError:
        return {'ok': False, 'error': 'node not found'}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'scan timed out (180s)'}


def _discover_script_path():
    _ensure_state()
    p = os.path.join(RUNTIME_DIR, 'discover_engine.cjs')
    if not os.path.exists(p):
        with open(p, 'w') as f:
            f.write(DISCOVER_ENGINE_JS)
    return p


DISCOVER_ENGINE_JS = r"""#!/usr/bin/env node
'use strict';
/* Website API discovery: load page (optionally with saved login profile),
   capture network requests, extract endpoints + keys from source/JS/localStorage. */
const fs = require('fs');
const path = require('path');
const os = require('os');
let chromium;
try {
  chromium = require(process.env.HARMONIKA_PLAYWRIGHT_MODULE||path.join(process.env.HARMONIKA_TERMINAL_HOME||path.join(os.homedir(),'.config','harmonika-terminal'),'browser-runtime','node_modules','playwright')).chromium;
} catch (e) {
  console.log(JSON.stringify({ok:false, error:'playwright not installed'}));
  process.exit(0);
}

(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const { url, profile_dir } = input;
  let context, browser;
  if (profile_dir && fs.existsSync(profile_dir)) {
    context = await chromium.launchPersistentContext(profile_dir, {headless: true, args:['--disable-blink-features=AutomationControlled']});
    browser = context;
  } else {
    browser = await chromium.launch({headless: true});
    context = await browser.newContext();
  }
  const page = await context.newPage();

  // Capture ALL network traffic
  const netLog = [];
  page.on('request', req => {
    const h = req.headers();
    netLog.push({url: req.url(), method: req.method(),
      auth: h['authorization'] || h['x-api-key'] || null,
      type: req.resourceType()});
  });
  page.on('response', res => {
    const i = netLog.findIndex(n => n.url === res.url());
    if (i >= 0) netLog[i].status = res.status();
  });

  await page.goto(url, {waitUntil: 'networkidle', timeout: 45000}).catch(()=>{});
  await page.waitForTimeout(3000);

  // localStorage + sessionStorage + cookies
  const storage = await page.evaluate(() => {
    const ls = {}, ss = {};
    try { for (let i=0;i<localStorage.length;i++){const k=localStorage.key(i); ls[k]=(localStorage.getItem(k)||'').slice(0,300);} } catch(e){}
    try { for (let i=0;i<sessionStorage.length;i++){const k=sessionStorage.key(i); ss[k]=(sessionStorage.getItem(k)||'').slice(0,300);} } catch(e){}
    const cfg = {};
    for (const v of ['__NEXT_DATA__','__NUXT__','APP_CONFIG','window.config','__APOLLO_STATE__']) {
      try { const val = eval(v.replace('window.','')); if (val) cfg[v] = JSON.stringify(val).slice(0,500); } catch(e){}
    }
    return {localStorage: ls, sessionStorage: ss, config: cfg};
  }).catch(()=>({localStorage:{},sessionStorage:{},config:{}}));
  const cookies = await context.cookies().catch(()=>[]);

  // Page source + script srcs
  const html = await page.content().catch(()=> '');
  const scriptSrcs = await page.evaluate(() =>
    [...document.querySelectorAll('script[src]')].map(s => s.src).slice(0, 40)
  ).catch(()=>[]);

  // Fetch first 12 JS bundles, scan for endpoints/keys
  const jsContents = [];
  for (const s of scriptSrcs.slice(0, 12)) {
    try {
      const r = await page.request.get(s, {timeout: 12000});
      const body = await r.text();
      if (body && body.length < 3_000_000) jsContents.push({src: s, body: body.slice(0, 800000)});
    } catch(e){}
  }
  try { await browser.close(); } catch(_){}

  // ── Pattern scanning in Node (mirror of python patterns) ──
  const keyPats = [
    ['api_key_literal', /["'](?:api[-_]?key|apikey|api[-_]?token|access[-_]?token|auth[-_]?token|client[-_]?secret|secret[-_]?key)["']\s*[:=]\s*["']([^"'\s]{12,200})["']/gi],
    ['jwt_inline', /(eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{5,})/g],
    ['google_key', /\b(AIza[0-9A-Za-z\-_]{35})\b/g],
    ['stripe_pk', /\b(pk_(?:live|test)_[0-9a-zA-Z]{20,})\b/g],
    ['github_pat', /\b(gh[pousr]_[A-Za-z0-9]{36,50})\b/g],
    ['bearer_assign', /\b(?:token|auth|bearer)[a-z_-]*\s*[:=]\s*["']([A-Za-z0-9\-_.]{28,})["']/gi],
  ];
  const epPats = [
    ['fetch_call', /fetch\(\s*["'`]([^"'`\s]{4,200})["'`]/g],
    ['api_path', /["'`](\/api\/[a-zA-Z0-9\/_\-{}.]{2,150}|\/v[1-3]\/[a-zA-Z0-9\/_\-{}.]{2,150}|\/graphql|\/api-docs|\/swagger\.json|\/openapi\.json)["'`]/g],
    ['base_url', /(?:baseURL|baseUrl|BASE_URL|API_URL|apiUrl|endpoint)\s*[:=]\s*["'`](https?:\/\/[^"'`\s]{4,200}|\/[^"'`\s]{2,100})["'`]/gi],
  ];
  function mask(s, show=6){ s=String(s); return s.length<=show*2 ? s.slice(0,2)+'***' : s.slice(0,show)+'...'+s.slice(-4)+' (len='+s.length+')'; }
  const foundKeys = [], foundEndpoints = [];
  const seenK = new Set(), seenE = new Set();
  const sources = [{src:'page_html', body:html.slice(0,900000)}, ...jsContents];
  for (const {src, body} of sources) {
    for (const [name, pat] of keyPats) {
      pat.lastIndex = 0; let m;
      while ((m = pat.exec(body)) !== null) {
        const val = m[1];
        const kId = name+':'+val.slice(0,20);
        if (!seenK.has(kId)) { seenK.add(kId); foundKeys.push({where:name, value:mask(val), found_in: src.split('/').pop().slice(0,60)});}
      }
    }
    for (const [name, pat] of epPats) {
      pat.lastIndex = 0; let m;
      while ((m = pat.exec(body)) !== null) {
        const val = m[1];
        const eId = val;
        if (!seenE.has(eId)) { seenE.add(eId); foundEndpoints.push({source:name, endpoint: val.slice(0,180), found_in: src.split('/').pop().slice(0,60)});}
      }
    }
  }

  // Network-derived API calls (XHR/fetch only)
  const apiCalls = netLog.filter(n => n.type === 'xhr' || n.type === 'fetch' || /\/api\/|\/v[1-3]\//.test(n.url));
  // Storage keys that look like tokens
  const storageTokens = [];
  for (const [k, v] of Object.entries({...storage.localStorage, ...storage.sessionStorage})) {
    if (/token|key|auth|session|secret|jwt/i.test(k) && v && v.length > 15) {
      storageTokens.push({name: k, value: mask(v, 8)});
    }
  }
  const authCookies = cookies.filter(c => /session|token|auth|jwt/i.test(c.name))
    .map(c => ({name: c.name, domain: c.domain, masked: mask(c.value, 6)}));

  console.log(JSON.stringify({
    ok: true, action: 'discover', url,
    api_endpoints: {from_network: apiCalls.slice(0,40).map(c => ({url: c.url.slice(0,200), method: c.method, status: c.status || null, has_auth_header: !!c.auth})),
                    from_source: foundEndpoints.slice(0,50)},
    api_keys_found: foundKeys.slice(0,25),
    storage_tokens: storageTokens.slice(0,20),
    auth_cookies: authCookies.slice(0,15),
    window_config: Object.keys(storage.config || {}),
    note: 'Masked values only. Use key_analyze for full type detection of a specific token.'
  }));
})().catch(e => console.log(JSON.stringify({ok:false, error: String(e).slice(0,300)})));
"""


# ═══════════════════ DOCS PARSE (OpenAPI/Swagger) ═══════════════════

def docs_parse(args):
    """Parse API docs: OpenAPI JSON/YAML URL, or auto-discover from site."""
    url = args.get('url', '') or args.get('spec_url', '')
    if not url:
        return {'ok': False, 'error': 'url required (OpenAPI/Swagger JSON or docs page)'}
    status, body, headers = _fetch(url, timeout=20)
    if status == 0:
        return {'ok': False, 'error': f'fetch failed: {body}'}

    spec = None
    # Try direct OpenAPI JSON
    try:
        candidate = json.loads(body)
        if 'openapi' in candidate or 'swagger' in candidate:
            spec = candidate
    except Exception:
        pass
    # Try YAML-ish (simple convert for common fields) → fallback: scan HTML for spec link
    if not spec:
        m = re.search(r'(https?://[^"\'\s]+(?:swagger\.json|openapi\.json|api-docs|swagger/v\d))', body)
        if m:
            status2, body2, _ = _fetch(m.group(1), timeout=20)
            try:
                spec = json.loads(body2)
            except Exception:
                pass
    if not spec:
        # HTML docs page: extract endpoints from table/code blocks
        return _parse_html_docs(url, body)

    return _parse_openapi(spec, url)


def _parse_openapi(spec, url):
    """Extract full endpoint map from OpenAPI spec object."""
    info = spec.get('info', {})
    base = (spec.get('servers') or [{}])[0].get('url', '')
    endpoints = []
    for path, methods in (spec.get('paths') or {}).items():
        for method, detail in methods.items():
            if method.lower() not in ('get', 'post', 'put', 'delete', 'patch', 'head', 'options'):
                continue
            params = []
            for p in detail.get('parameters', []) or []:
                params.append({'name': p.get('name'), 'in': p.get('in'), 'required': p.get('required', False),
                               'type': (p.get('schema') or {}).get('type', p.get('type'))})
            rb = detail.get('requestBody') or {}
            content = list((rb.get('content') or {}).keys())
            security = list((detail.get('security') or [{}])[0].keys()) if detail.get('security') else []
            endpoints.append({
                'method': method.upper(), 'path': path,
                'summary': detail.get('summary') or detail.get('description', '')[:100] or '',
                'params': params[:10], 'body_content_types': content,
                'auth': security or ('inherited (global)' if spec.get('security') else 'none/public'),
                'operation_id': detail.get('operationId'),
            })
    # Group by tag/category
    groups = {}
    for path, methods in (spec.get('paths') or {}).items():
        for method, detail in methods.items():
            if not isinstance(detail, dict):
                continue
            for tag in (detail.get('tags') or ['untagged']):
                groups.setdefault(tag, []).append(f'{method.upper()} {path}')
    return {
        'ok': True, 'action': 'docs_parse', 'spec_url': url,
        'api_title': info.get('title'), 'api_version': info.get('version'),
        'description': (info.get('description') or '')[:200],
        'base_url': base, 'openapi_version': spec.get('openapi') or spec.get('swagger'),
        'total_endpoints': len(endpoints),
        'auth_schemes': list((spec.get('components', {}).get('securitySchemes') or {}).keys()),
        'endpoints': endpoints[:80],
        'categories': {k: v[:15] for k, v in list(groups.items())[:20]},
    }


def _parse_html_docs(url, html):
    """Fallback: extract endpoint-like patterns from docs HTML."""
    endpoints = set()
    for pat in (
        r'(?:GET|POST|PUT|DELETE|PATCH)\s+(/[a-zA-Z0-9/_\-{}.]{2,150})',
        r'["\'`](/[a-zA-Z0-9/_\-{}]*api[a-zA-Z0-9/_\-{}.]{0,100}|/api/[a-zA-Z0-9/_\-{}.]{2,100}|/v[1-3]/[a-zA-Z0-9/_\-{}.]{2,100})["\'`]',
        r'href="(/[^"]*(?:api|docs|reference)[^"]*)"',
    ):
        for m in re.finditer(pat, html):
            endpoints.add(m.group(1) if m.lastindex else m.group(0))
    code_blocks = re.findall(r'<code[^>]*>([^<]{4,200})</code>', html)
    return {'ok': True, 'action': 'docs_parse', 'spec_url': url, 'format': 'html_docs',
            'note': 'OpenAPI spec not detected; extracted from HTML patterns.',
            'possible_endpoints': sorted(endpoints)[:50],
            'code_samples': [c.strip()[:120] for c in code_blocks[:20]],
            'hint': 'If site has /swagger.json or /openapi.json, pass that URL directly.'}


# ═══════════════════ PROBE ═══════════════════

def probe(args):
    """Probe base URL for API paths: which exist, auth required, response format."""
    base = args.get('base_url', '') or args.get('url', '')
    api_key = args.get('key', '') or args.get('token', '')
    if not base:
        return {'ok': False, 'error': 'base_url required'}
    base = base.rstrip('/')
    headers = {'Authorization': f'Bearer {api_key}', 'X-API-Key': api_key} if api_key else {}
    results = []
    for p in COMMON_API_PATHS:
        full = base + p
        status, body, resp_headers = _fetch(full, timeout=8, headers=headers)
        if status == 0:
            continue
        ct = resp_headers.get('Content-Type', resp_headers.get('content-type', ''))
        is_json = 'json' in ct.lower() or body.strip().startswith(('{', '['))
        snippet = ''
        if is_json:
            try:
                d = json.loads(body)
                snippet = json.dumps(d, default=str)[:150]
            except Exception:
                snippet = body[:100]
        else:
            snippet = re.sub(r'<[^>]+>', ' ', body)[:80].strip()
        results.append({'path': p, 'status': status, 'content_type': ct.split(';')[0],
                        'is_json': is_json, 'preview': snippet})
        time.sleep(0.15)  # gentle
    found = [r for r in results if r['status'] < 400]
    protected = [r for r in results if r['status'] in (401, 403)]
    openapi = [r for r in results if 'openapi' in r['path'] or 'swagger' in r['path'] or 'api-docs' in r['path']]
    return {
        'ok': True, 'action': 'probe', 'base_url': base, 'authenticated_probe': bool(api_key),
        'reachable': len(found), 'protected': [r['path'] for r in protected],
        'api_docs_found': [r['path'] for r in openapi if r['status'] < 400],
        'public_endpoints': [r for r in found if 'json' in (r['content_type'] or '') or r['is_json']][:15],
        'all_results': results,
        'note': '401/403 paths exist but need auth. Docs paths → feed to docs_parse.',
    }


# ═══════════════════ MONITOR (live API traffic capture) ═══════════════════

MONITOR_STATE = os.path.join(RUNTIME_DIR, 'monitor_state.json')


def monitor_start(args):
    """Capture bounded request mechanics; no authorization values or bodies."""
    _ensure_state()
    try:
        with open(MONITOR_STATE,encoding='utf-8') as stream:previous=json.load(stream)
    except (OSError,ValueError):previous={}
    if previous.get('status') in ('starting','running','stop_requested'):return {'ok':False,'error':'monitor_in_progress','safe_to_retry':False}
    url=_safe_url(args.get('url','https://example.com'))
    script=os.path.join(RUNTIME_DIR,'monitor_engine.cjs')
    with open(script,'w',encoding='utf-8') as stream:stream.write(MONITOR_ENGINE_JS)
    node=os.environ.get('HARMONIKA_BROWSER_NODE') or os.environ.get('HARMONIKA_NODE') or shutil.which('node')
    if not node:return {'ok':False,'error':'node_runtime_unavailable'}
    run_id=uuid.uuid4().hex
    capture=os.path.join(RUNTIME_DIR,'monitor_capture.json');stop=os.path.join(RUNTIME_DIR,'monitor_stop.json')
    if os.path.exists(capture):os.unlink(capture)
    _private_json(MONITOR_STATE,{'status':'starting','run_id':run_id,'ts':time.time()})
    proc=subprocess.Popen([node,script,url,RUNTIME_DIR,run_id],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    time.sleep(0.25)
    if proc.poll() is not None:
        _private_json(MONITOR_STATE,{'status':'failed','run_id':run_id,'ts':time.time()})
        return {'ok':False,'error':'browser_runtime_unavailable','exit_code':proc.returncode}
    return {'ok':True,'action':'monitor_start','status':'starting','metadata_only':True,'captured_so_far':len(_read_capture().get('calls',[]))}


def _read_capture():
    cap_file = os.path.join(RUNTIME_DIR, 'monitor_capture.json')
    if os.path.exists(cap_file):
        try:
            with open(cap_file,encoding='utf-8') as stream:return json.load(stream)
        except Exception:
            pass
    return {'calls': []}


def monitor_status(args):
    cap = _read_capture()
    calls = cap.get('calls', [])
    api_calls = [c for c in calls if c.get('type') in ('xhr', 'fetch') or '/api' in c.get('url', '') or '/v' in c.get('url', '')]
    # Group by host+path pattern
    patterns = {}
    for c in api_calls:
        p = urllib.parse.urlparse(c['url'])
        path = re.sub(r'\d+', '{id}', p.path)
        key = f'{p.netloc}{path}'
        patterns.setdefault(key, {'count': 0, 'methods': set(), 'auth': False})
        patterns[key]['count'] += 1
        patterns[key]['methods'].add(c.get('method', 'GET'))
        if c.get('has_auth'):
            patterns[key]['auth'] = True
    return {'ok': True, 'action': 'monitor_status',
            'total_requests': len(calls), 'api_requests': len(api_calls),
            'unique_api_patterns': [{'pattern': k, 'calls': v['count'],
                                     'methods': sorted(v['methods']), 'needs_auth': v['auth']}
                                    for k, v in sorted(patterns.items(), key=lambda x: -x[1]['count'])[:40]],
            'auth_present':sum(1 for c in api_calls if c.get('has_auth')),'metadata_only':True}


def monitor_stop(args):
    _ensure_state()
    report=monitor_status({})
    try:
        with open(MONITOR_STATE,encoding='utf-8') as stream:state=json.load(stream)
    except (OSError,ValueError):return {'ok':False,'error':'monitor_not_registered'}
    run_id=state.get('run_id','')
    if not re.fullmatch(r'[a-f0-9]{32}',run_id):return {'ok':False,'error':'monitor_recovery_required','safe_to_retry':False}
    _private_json(os.path.join(RUNTIME_DIR,'monitor_stop.json'),{'run_id':run_id})
    _private_json(MONITOR_STATE,{'status':'stop_requested','run_id':run_id,'ts':time.time()})
    # Cooperative stop of this registered monitor only; never global pkill.
    ts=datetime.now().strftime('%Y%m%d-%H%M%S')
    rp=os.path.join(REPORT_DIR,'api-monitor-'+ts+'.json');_private_json(rp,report)
    return {**report,'ok':True,'action':'monitor_stop','status':'stop_requested','report_file':rp,'metadata_only':True}


MONITOR_ENGINE_JS = r"""'use strict';
const fs=require('fs'),path=require('path'),os=require('os');
const [url,runtime,runID]=process.argv.slice(2);
const terminal=process.env.HARMONIKA_TERMINAL_HOME||path.join(os.homedir(),'.config','harmonika-terminal');
const playwright=process.env.HARMONIKA_PLAYWRIGHT_MODULE||path.join(terminal,'browser-runtime','node_modules','playwright');
const {chromium}=require(playwright);
const CAP=path.join(runtime,'monitor_capture.json'),STOP=path.join(runtime,'monitor_stop.json'),STATE=path.join(runtime,'monitor_state.json');
const writeState=status=>{const tmp=STATE+'.tmp-'+runID;fs.writeFileSync(tmp,JSON.stringify({status,run_id:runID,ts:Date.now()/1000}),{mode:0o600});fs.renameSync(tmp,STATE)};
(async()=>{
 const browser=await chromium.launch({headless:false});
 const context=await browser.newContext(),page=await context.newPage();let calls=[],stopping=false,timer;
 browser.on('disconnected',()=>{if(timer)clearInterval(timer);writeState('stopped')});
 const flush=()=>{const tmp=CAP+'.tmp';fs.writeFileSync(tmp,JSON.stringify({calls,updated:Date.now(),metadata_only:true}),{mode:0o600});fs.renameSync(tmp,CAP)};
 context.on('request',req=>{
  let value;try{value=new URL(req.url())}catch{return}value.search='';value.hash='';value.username='';value.password='';
  const h=req.headers();calls.push({url:value.toString(),method:req.method(),type:req.resourceType(),has_auth:!!(h.authorization||h['x-api-key']||h['x-auth-token']),ts:Date.now()});
  calls=calls.slice(-1000);
 });
 await page.goto(url,{waitUntil:'domcontentloaded'}).catch(()=>{});
 writeState('running');
 timer=setInterval(async()=>{
  try{flush();const stop=JSON.parse(fs.readFileSync(STOP,'utf8'));if(stop.run_id===runID&&!stopping){stopping=true;clearInterval(timer);await browser.close();}}catch{}
 },1000);
})();
"""


# ═══════════════════ DISPATCH & REGISTRY ═══════════════════

def _dispatch(args):
    action = args.get('action', 'key_analyze')
    if action == 'key_analyze':
        return key_analyze(args)
    if action == 'discover':
        return discover(args)
    if action == 'docs_parse':
        return docs_parse(args)
    if action == 'probe':
        return probe(args)
    if action == 'monitor_start':
        return monitor_start(args)
    if action == 'monitor_status':
        return monitor_status(args)
    if action == 'monitor_stop':
        return monitor_stop(args)
    return {'ok': False, 'error': f'unknown action: {action}. Use: key_analyze, discover, docs_parse, probe, monitor_start, monitor_status, monitor_stop'}


API_INTEL_TOOLS = {
    'api_intelligence': {
        'description': 'API mastery engine: key_analyze=detect API key type (JWT decode, AWS/Google/Stripe/GitHub/OpenAI patterns, expiry, risk); '
                       'discover=scan website for API endpoints + keys (source, JS bundles, localStorage, cookies, network); '
                       'docs_parse=parse OpenAPI/Swagger/API docs into full endpoint map (methods, params, auth, categories); '
                       'probe=test base URL: which endpoints exist, public vs 401-protected, find swagger.json; '
                       'monitor_start/status/stop=launch browser, record ALL API traffic while you browse (DevTools auto-log).',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string',
                           'enum': ['key_analyze', 'discover', 'docs_parse', 'probe',
                                    'monitor_start', 'monitor_status', 'monitor_stop']},
                'key': {'type': 'string', 'description': 'API key/token to analyze (masked in output)'},
                'url': {'type': 'string', 'description': 'Website or docs URL to scan'},
                'base_url': {'type': 'string', 'description': 'API base URL for probe'},
                'token': {'type': 'string', 'description': 'Optional API key for authenticated probe'},
            },
            'required': ['action'],
        },
    },
}

API_INTEL_HANDLERS = {'api_intelligence': _dispatch}
