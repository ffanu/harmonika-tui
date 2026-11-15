"""Harmonika TUI configuration auditor: mechanics-only, read-only findings.

`audit_config(value)` inspects an already-parsed configuration object and
reports structured findings that distinguish legacy field mechanics
(`permission` / `bash` / `task`) from native V2 mechanics (`permissions` /
`shell` / `subagent`), unpinned third-party npm/git plugins, remote untrusted
public API hosts, and enabled auto-update flags.

The audit is mechanics-only. Findings carry stable codes and field paths,
never configuration values, passwords, provider keys, plugin names, hosts,
model responses or personal data. The input is never modified, migrated or
rewritten: audit only. Engine generation is derived from field mechanics
alone; a benign Harmonika custom version string is branding metadata and is
never treated as proof of any engine generation. Existing global settings may
use compatibility aliases, so a finding is not proof that the setting is
ignored.
"""
import ipaddress
import json
import math
import re
from urllib.parse import urlsplit

__all__ = ['AUDIT_NAME', 'SCHEMA_VERSION', 'SEVERITIES', 'LIMITATIONS',
           'TRUSTED_API_HOSTS', 'TRUSTED_DOC_HOSTS', 'ConfigAuditError',
           'audit_config']

AUDIT_NAME = 'tui_config_audit'
SCHEMA_VERSION = 1
SEVERITIES = ('critical', 'high', 'medium', 'low', 'info')

TRUSTED_API_HOSTS = frozenset({'chat.harmonika.id'})
TRUSTED_DOC_HOSTS = frozenset({'hai.harmonika.id'})

MAX_BYTES = 524288
MAX_DEPTH = 12
MAX_VISITS = 200000
MAX_HOST_TEXT = 2000

LEGACY_FIELD_CODES = {
    'permission': 'legacy_permission_field',
    'bash': 'legacy_bash_field',
    'task': 'legacy_task_field',
}
NATIVE_FIELD_CODES = {
    'permissions': 'native_permissions_field',
    'shell': 'native_shell_field',
    'subagent': 'native_subagent_field',
}

GLOBAL_AUTO_UPDATE_KEYS = frozenset((
    'auto_update', 'auto-update', 'autoupdate', 'auto_update_enabled',
    'automatic_update', 'automatic_updates', 'automatic-updates',
    'auto_upgrade', 'auto-upgrade', 'autoupdater',
))
NESTED_AUTO_UPDATE_KEYS = frozenset(('auto', 'automatic', 'automatically'))
UPDATE_PARENT_KEYS = frozenset(('update', 'updates', 'updater'))

EXPLICIT_API_KEYS = frozenset((
    'api', 'api_url', 'api_base', 'api_base_url', 'api_host', 'api_endpoint',
    'api_server', 'public_api', 'public_api_host', 'public_api_url',
    'model_api', 'model_api_url', 'chat_api', 'chat_api_url', 'gateway_url',
))
CONTEXT_API_KEYS = frozenset(('url', 'host', 'endpoint', 'base_url', 'server'))
API_CONTEXT_COMPONENTS = frozenset(('provider', 'providers', 'model', 'models', 'gateway', 'upstream'))
SAFE_PATH_FIELDS = frozenset(LEGACY_FIELD_CODES) | frozenset(NATIVE_FIELD_CODES) | GLOBAL_AUTO_UPDATE_KEYS | NESTED_AUTO_UPDATE_KEYS | UPDATE_PARENT_KEYS | EXPLICIT_API_KEYS | CONTEXT_API_KEYS | API_CONTEXT_COMPONENTS | frozenset(('options', 'plugins', 'plugin', 'providers', 'models', 'permissions', 'agents', 'tools', 'settings', 'baseurl', 'baseURL'))

LOCAL_HOST_SUFFIXES = ('.localhost', '.local', '.internal', '.lan',
                       '.home.arpa', '.home', '.corp')

THIRD_PARTY_KINDS = frozenset(('npm', 'registry', 'git', 'github', 'gitlab',
                               'remote', 'url'))
LOCAL_KINDS = frozenset(('local', 'builtin', 'native', 'embedded', 'file',
                         'path', 'inline'))
GIT_KINDS = frozenset(('git', 'github', 'gitlab'))
GIT_PIN_FIELDS = ('commit', 'sha', 'ref', 'tag', 'rev')
INTEGRITY_FIELDS = ('integrity', 'shasum', 'sha256', 'sha512')
MUTABLE_REFS = frozenset(('head', 'main', 'master', 'develop', 'dev', 'trunk'))
UNPINNED_VERSION_RE = re.compile(r'\A[\^~*><=]|latest\Z|\||\s-\s')

LIMITATIONS = (
    'Mechanics-only audit of field names and structure; findings are not proof that a setting is ignored, malicious or broken.',
    'No configuration value, credential, provider key, plugin name, host, model response or personal data is ever echoed.',
    'The configuration is inspected read-only; no migration, rewrite or deletion is performed.',
    'Engine generation is derived from field mechanics only; product and version strings are branding, never engine proof.',
    'Trusted public API host is chat.harmonika.id; product documentation may use hai.harmonika.id.',
)


class ConfigAuditError(ValueError):
    """Machine-coded error; the message never carries caller-supplied content."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise ConfigAuditError(code)


def _bounded(value):
    stack = [(value, 0)]
    visits = 0
    while stack:
        item, depth = stack.pop()
        visits += 1
        _need(depth <= MAX_DEPTH and visits <= MAX_VISITS, 'input_complexity_limit')
        kind = type(item)
        if kind is dict:
            _need(all(type(key) is str for key in item), 'invalid_json_key')
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif kind is list:
            stack.extend((child, depth + 1) for child in item)
        elif kind is str:
            _need(not any(0xD800 <= ord(char) <= 0xDFFF for char in item), 'invalid_unicode')
        elif kind is float:
            _need(math.isfinite(item), 'invalid_number')
        elif kind is int:
            _need(item.bit_length() <= 128, 'invalid_integer')
        elif kind not in (bool, type(None)):
            raise ConfigAuditError('invalid_json')
    try:
        raw = json.dumps(value, allow_nan=False, ensure_ascii=True).encode()
    except (TypeError, ValueError):
        raise ConfigAuditError('invalid_json') from None
    _need(len(raw) <= MAX_BYTES, 'input_byte_limit')


def _is_local_host(host):
    bare = host.strip('[]')
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        lowered = bare.lower()
        return (lowered == 'localhost' or lowered.endswith(LOCAL_HOST_SUFFIXES)
                or lowered == '0.0.0.0')
    return (address.is_loopback or address.is_private or address.is_link_local
            or address.is_reserved or address.is_multicast
            or address.is_unspecified)


def _extract_host(value):
    text = value.strip()
    if not text or len(text) > MAX_HOST_TEXT or any(char.isspace() for char in text):
        return None
    if '://' in text:
        parts = urlsplit(text)
        if parts.scheme.lower() not in ('http', 'https', 'ws', 'wss'):
            return None
        host = parts.hostname
    else:
        if text.startswith(('/', '.')):
            return None
        host = text.split('/', 1)[0].rsplit('@', 1)[-1].split(':', 1)[0]
    host = (host or '').strip('[]').lower()
    return host or None


def _is_api_key(key_lower, ancestors):
    if key_lower in EXPLICIT_API_KEYS:
        return True
    if key_lower in CONTEXT_API_KEYS:
        return any(component.startswith('api')
                   or component in API_CONTEXT_COMPONENTS
                   or 'endpoint' in component
                   for component in ancestors)
    return False


def _trusted_host(key_lower, ancestors, host):
    if host in TRUSTED_API_HOSTS:
        return True
    if host in TRUSTED_DOC_HOSTS:
        return ('doc' in key_lower
                or any('doc' in component for component in ancestors))
    return False


def _plugin_descriptor(entry):
    if isinstance(entry, str):
        lowered = entry.strip().lower()
        if lowered.startswith('npm:'):
            spec = entry[4:]
            version = spec.rsplit('@', 1)[1] if '@' in spec else None
            return 'npm', {'version': version}
        if lowered.startswith(('git+', 'git@')):
            return 'git', {}
        return None, {}
    if not isinstance(entry, dict):
        return None, {}
    merged = dict(entry)
    source = entry.get('source')
    if isinstance(source, dict):
        merged.update(source)
    kind = merged.get('kind', merged.get('type', merged.get('provider')))
    kind = str(kind).strip().lower() if kind is not None else ''
    url = str(merged.get('url') or merged.get('src') or '').strip().lower()
    if not kind:
        if url.startswith(('git+', 'git@')):
            kind = 'git'
        elif url.startswith(('http://', 'https://')):
            kind = 'url'
    return kind, merged


def _check_plugins(entries, base_path, add):
    for index, entry in enumerate(entries):
        kind, spec = _plugin_descriptor(entry)
        if not kind or kind in LOCAL_KINDS or kind not in THIRD_PARTY_KINDS:
            continue
        where = '%s[%d]' % (base_path, index)
        if kind in GIT_KINDS:
            ref = None
            for field in GIT_PIN_FIELDS:
                value = spec.get(field)
                if isinstance(value, str) and value.strip():
                    ref = value.strip().lower()
                    break
            if ref is None:
                add('plugin_ref_unpinned', where, 'high')
            elif ref in MUTABLE_REFS:
                add('plugin_ref_mutable', where, 'high')
        elif kind in ('npm', 'registry'):
            version = spec.get('version')
            text = version.strip() if isinstance(version, str) else ''
            if not text or UNPINNED_VERSION_RE.search(text):
                add('plugin_version_unpinned', where, 'high')
            elif not any(spec.get(field) for field in INTEGRITY_FIELDS):
                add('plugin_integrity_missing', where, 'medium')
        else:
            if not any(spec.get(field) for field in INTEGRITY_FIELDS):
                add('plugin_integrity_missing', where, 'medium')


def _walk(node, path, depth, ancestors, add):
    if depth > MAX_DEPTH:
        raise ConfigAuditError('input_complexity_limit')
    if isinstance(node, dict):
        for key in sorted(node):
            if not isinstance(key, str):
                raise ConfigAuditError('invalid_json_key')
            child = node[key]
            lower = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', key).replace('-', '_').lower()
            display = key if key in SAFE_PATH_FIELDS or lower in SAFE_PATH_FIELDS else '[entry]'
            child_path = '%s.%s' % (path, display) if path else display
            if lower in LEGACY_FIELD_CODES:
                add(LEGACY_FIELD_CODES[lower], child_path, 'medium')
            if lower in NATIVE_FIELD_CODES:
                add(NATIVE_FIELD_CODES[lower], child_path, 'info')
            if child is True:
                if lower in GLOBAL_AUTO_UPDATE_KEYS:
                    add('auto_update_enabled', child_path, 'high')
                elif (lower in NESTED_AUTO_UPDATE_KEYS
                      and ancestors and ancestors[-1] in UPDATE_PARENT_KEYS):
                    add('auto_update_enabled', child_path, 'high')
            if isinstance(child, str) and _is_api_key(lower, ancestors):
                host = _extract_host(child)
                if host and not _is_local_host(host) and not _trusted_host(lower, ancestors, host):
                    add('untrusted_api_host', child_path, 'high',
                        trusted_hosts=sorted(TRUSTED_API_HOSTS))
            if lower == 'plugins' and isinstance(child, list):
                _check_plugins(child, child_path, add)
            _walk(child, child_path, depth + 1, ancestors + (lower,), add)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _walk(item, '%s[%d]' % (path, index), depth + 1, ancestors, add)
    elif node is None or isinstance(node, (bool, int, float, str)):
        return
    else:
        raise ConfigAuditError('invalid_json')


def audit_config(value):
    """Audit a parsed configuration object; returns mechanics-only findings.

    The input is never mutated. The result contains stable codes and field
    paths only, plus static metadata; configuration values are never echoed.
    """
    _need(type(value) is dict, 'invalid_config')
    _bounded(value)
    findings = []

    def add(code, path, severity, **extra):
        finding = {'code': code, 'path': path, 'severity': severity}
        finding.update(extra)
        findings.append(finding)

    _walk(value, '', 0, (), add)
    findings.sort(key=lambda finding: (finding['path'], finding['code']))
    legacy = any(finding['code'].startswith('legacy_') for finding in findings)
    native = any(finding['code'].startswith('native_') for finding in findings)
    if legacy and native:
        generation = 'mixed'
    elif legacy:
        generation = 'legacy'
    elif native:
        generation = 'native_v2'
    else:
        generation = 'unknown'
    summary = {'total': len(findings)}
    for severity in SEVERITIES:
        summary[severity] = sum(1 for finding in findings
                                if finding['severity'] == severity)
    return {
        'audit': AUDIT_NAME,
        'schema': SCHEMA_VERSION,
        'audit_only': True,
        'config_preserved': True,
        'config_modified': False,
        'engine_generation': generation,
        'findings': findings,
        'summary': summary,
        'finding_codes': sorted({finding['code'] for finding in findings}),
        'limitations': list(LIMITATIONS),
    }
