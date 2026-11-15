"""Sanitize registered MCP results before model transport; never log raw values.

This is defense in depth, not a substitute for per-tool permissions/sandboxing.
Only dedicated connectors may read a secret and must return a private reference.
"""
import functools
import json
import re

MASK = '[REDACTED_SECRET]'
KEY = re.compile(r'^(?:authorization|proxy_authorization|cookie|set_cookie|password|passwd|pwd|(?:[a-z0-9]+_)*(?:api_key|apikey|access_token|refresh_token|secret_key|client_secret|bot_token|private_key|credential|credentials|password|passwd|secret|token|cookie))$', re.I)
ENV_REFERENCE = re.compile(r'''(?:process\.env\.[A-Z][A-Z0-9_]*|os\.environ\[['"][A-Z][A-Z0-9_]*['"]\]|(?:os\.getenv|os\.environ\.get|getenv|System\.getenv)\(['"][A-Z][A-Z0-9_]*['"]\)|\$\{[A-Z][A-Z0-9_]*\}|''|""|null|None|undefined)''')


def _assignment(match):
    prefix = match.group(1)
    return match.group(0) if ENV_REFERENCE.fullmatch(match.group(0)[len(prefix):]) else prefix+MASK


PATTERNS = [
    (r'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|$)', MASK),
    (r'\b(?:sk-(?:proj-|svcacct-)?|hai_live_|hst_|hgw_)[A-Za-z0-9_-]{16,}\b', MASK),
    (r'\bBearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer '+MASK),
    (r'\b[0-9]{6,12}:[A-Za-z0-9_-]{30,}\b', MASK),
    (r'(https?://)[^\s/:@]+:[^\s/@]+@', r'\g<1>'+MASK+'@'),
    (r'''((?:[?&]|\b)(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|authorization|cookie|secret[_-]?key)["']?\s*[=:]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s&,;\r\n]+)''', _assignment),
    (r'''((?:^|\s)--(?:password|passwd|token|api-key|secret)(?:=|\s+))(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s]+)''', r'\g<1>'+MASK),
    (r'(\b(?:Cookie|Set-Cookie):\s*)[^\r\n]+', r'\g<1>'+MASK),
]
PATTERNS = [(re.compile(p, re.I if 'PRIVATE KEY' not in p else 0), v) for p, v in PATTERNS]


def scrub_text(text):
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub(value):
    nodes = 0
    def walk(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 100000 or depth > 24:
            raise ValueError('tool_payload_complexity')
        if isinstance(item, str):
            # Parse object/array text only; scalar parsing would recurse forever.
            if item.lstrip().startswith(('{', '[')):
                try:
                    parsed = json.loads(item)
                except (ValueError, RecursionError):
                    pass
                else:
                    if isinstance(parsed, (dict, list)):
                        clean = walk(parsed, depth + 1)
                        return item if clean == parsed else json.dumps(clean, ensure_ascii=False)
            return scrub_text(item)
        if isinstance(item, dict):
            return {k: MASK if KEY.fullmatch(re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', str(k)).replace('-', '_')) and v is not None and type(v) is not bool else walk(v, depth + 1) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v, depth + 1) for v in item]
        if item is None or type(item) in (bool, int, float):
            return item
        raise ValueError('invalid_tool_payload')
    return walk(value, 0)


def validate_request(name, args):
    if type(args) is not dict:
        raise ValueError('invalid_tool_arguments')
    if scrub(args) != args or MASK in json.dumps(args):
        raise ValueError('tool_secret_reference_required')
    for key in ('path', 'file_path', 'source', 'target', 'command', 'cmd', 'code', 'script'):
        val = args.get(key)
        if not isinstance(val, str):
            continue
        if re.search(r'''(?:^|[/\\\s'\"])(?:\.ssh|\.aws|\.gnupg|secrets|credentials)(?:[/\\]|$)|(?:^|[/\\\s'\"])\.env(?!\.(?:example|sample|template)\b)(?:\b|$)|\bid_(?:rsa|ed25519)\b|/etc/(?:shadow|gshadow)\b|\b(?:credentials\.json|server\.key)\b''', val, re.I):
            raise ValueError('tool_secret_reference_required')
        if re.search(r'exec|shell|bash|terminal', name, re.I) and re.search(r'\bprintenv\b|(?:^|[;&|])\s*env\s*(?:$|[;&|])', val):
            raise ValueError('tool_secret_reference_required')


def protect(name, handler):
    if getattr(handler, '_harmonika_firewall', False):
        return handler
    @functools.wraps(handler)
    def wrapped(args):
        try:
            validate_request(name, args)
            return scrub(handler(args))
        except Exception as error:
            code = str(error) if type(error) is ValueError and str(error) in ('tool_secret_reference_required', 'invalid_tool_arguments', 'tool_payload_complexity', 'invalid_tool_payload') else 'tool_operation_failed'
            return {'ok': False, 'error': code}
    wrapped._harmonika_firewall = True
    return wrapped
