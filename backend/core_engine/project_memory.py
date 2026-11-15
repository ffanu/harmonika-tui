"""Explicit curated project memory over the encrypted tenant Store.

Project mode reuses the four existing memory skill IDs; requests that carry a
``project_id`` are routed here by the registry. Entries are limited to curated
``{kind, text}`` values behind explicit consent, obvious secret material and
identifiers are refused with stable codes that never echo content, and the
scope is derived with SHA-256 under a reserved prefix so direct generic access
to the namespace can be refused. Detection is a bounded heuristic, not a
guarantee that every personal string is found. Stored text is data only: it is
never interpreted as instructions and no user history is captured
automatically.
"""
import hashlib
import re

from .state import StateError

SCOPE_PREFIX = 'p:'
MEMORY_SKILLS = frozenset(('memory.set', 'memory.get', 'memory.search', 'memory.clear'))
PROJECT_KINDS = ('decision', 'convention', 'verified_command')
MAX_PROJECT_ID = 64
MAX_KEY = 128
MAX_TEXT = 2000
MAX_QUERY = 100
TTL_MIN, TTL_MAX = 60, 604800
TTL_DEFAULT = 86400

_PROJECT_ID = re.compile(r'[\x21-\x7e]{1,64}')
_KEY_TOKENS = frozenset((
    'password', 'passwd', 'pwd', 'secret', 'secrets', 'token', 'tokens',
    'apikey', 'credential', 'credentials', 'cookie', 'cookies', 'bearer',
    'authorization', 'csrf', 'session', 'cardnumber', 'creditcard', 'cvv',
    'cvv2', 'cvc', 'iban', 'ssn', 'passport', 'taxid', 'npwp', 'nik',
    'aadhaar', 'email', 'message', 'messages', 'chat', 'transcript',
    'transcripts', 'conversation', 'history', 'inbox',
    'dob', 'address', 'alamat', 'phone', 'telephone', 'telepon',
))
_KEY_PHRASES = (
    'api key', 'access key', 'private key', 'client secret', 'session id',
    'card number', 'credit card', 'tax id', 'social security', 'chat history',
    'message history', 'chat log',
    'first name', 'last name', 'full name', 'customer name', 'nama pelanggan',
    'date of birth', 'tanggal lahir', 'phone number',
)
_ASSIGNMENT = re.compile(
    r'\b(?:password|passwd|pwd|secret|token|api[_\- ]?key|access[_\- ]?key|'
    r'client[_\- ]?secret|private[_\- ]?key|cookie|session[_\- ]?id|'
    r'authorization|credential|ssn|passport|npwp|nik|aadhaar|iban|cvv|cvc|'
    r'card[_\- ]?number)\b\s*[":=]\s*\S{3,}', re.I)
_PRIVATE_KEY = re.compile(r'-----\s*begin [a-z0-9 ]*private key\s*-----', re.I)
_BEARER = re.compile(r'\bbearer\s+[a-z0-9._~+/=-]{16,}', re.I)
_EMAIL = re.compile(r'\b[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b', re.I)
_TOKEN_SHAPES = (
    re.compile(r'\b(?:hai_live_|hst_|hgw_)[A-Za-z0-9_-]{16,}\b'),
    re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,255}\b'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'),
    re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b'),
)
_CARD_SHAPE = re.compile(r'\b(?:\d[ -]?){12,18}\d\b')
_IBAN_SHAPE = re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b')
_CHAT_PASTE = re.compile(
    r'\b(?:messages?|transcript|conversation|chat)\b\s*[":=]\s*[\[{"\d]', re.I)


def project_scope(project_id):
    digest = hashlib.sha256(project_id.encode('ascii')).hexdigest()
    return SCOPE_PREFIX + digest[:62]


def is_reserved_scope(scope):
    return type(scope) is str and scope.startswith(SCOPE_PREFIX)


def _fields(value, allowed, required):
    if type(value) is not dict or set(value) - set(allowed) or not set(required) <= set(value):
        raise StateError('invalid_input_fields')


def _project_id(value):
    if type(value) is not str or not _PROJECT_ID.fullmatch(value):
        raise StateError('invalid_project_id')
    return value


def _text(value, field, maximum, *, allow_newline=False):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise StateError('invalid_' + field)
    for char in value:
        code = ord(char)
        if code == 10 or code == 9:
            if not allow_newline:
                raise StateError('invalid_' + field)
        elif code < 32 or code == 127:
            raise StateError('invalid_' + field)
    return value


def _integer(value, name, low, high):
    if type(value) is not int or type(value) is bool or not low <= value <= high:
        raise StateError('invalid_' + name)
    return value


def _luhn(digits):
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = ord(char) - 48
        if index & 1:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _iban_valid(candidate):
    rearranged = candidate[4:] + candidate[:4]
    numeric = ''.join(str(int(char, 36)) for char in rearranged)
    return int(numeric) % 97 == 1


def _reject_sensitive(key, text):
    normalized = re.sub(r'[^a-z0-9]+', ' ', key.lower()).strip()
    tokens = set(normalized.split())
    if tokens & _KEY_TOKENS or any(phrase in normalized for phrase in _KEY_PHRASES):
        raise StateError('sensitive_content_rejected')
    combined = key + '\n' + text
    checks = (_ASSIGNMENT.search(combined.replace('_', ' ')), _PRIVATE_KEY.search(combined),
              _BEARER.search(combined), _EMAIL.search(combined), _CHAT_PASTE.search(combined))
    if any(checks) or any(shape.search(combined) for shape in _TOKEN_SHAPES):
        raise StateError('sensitive_content_rejected')
    for candidate in _CARD_SHAPE.findall(combined):
        digits = candidate.replace(' ', '').replace('-', '')
        if 13 <= len(digits) <= 19 and _luhn(digits):
            raise StateError('sensitive_content_rejected')
    for candidate in _IBAN_SHAPE.findall(combined):
        if _iban_valid(candidate):
            raise StateError('sensitive_content_rejected')


def _validate_set(value):
    _fields(value, ('project_id', 'consent', 'key', 'value', 'ttl_seconds'),
            ('project_id', 'consent', 'key', 'value'))
    project_id = _project_id(value['project_id'])
    if value['consent'] is not True:
        raise StateError('consent_required')
    key = _text(value['key'], 'key', MAX_KEY)
    entry = value['value']
    if type(entry) is not dict or set(entry) != {'kind', 'text'}:
        raise StateError('invalid_value')
    if type(entry['kind']) is not str or entry['kind'] not in PROJECT_KINDS:
        raise StateError('invalid_kind')
    text = _text(entry['text'], 'text', MAX_TEXT, allow_newline=True)
    ttl = _integer(value.get('ttl_seconds', TTL_DEFAULT), 'ttl_seconds', TTL_MIN, TTL_MAX)
    _reject_sensitive(key, text)
    return {'scope': project_scope(project_id), 'key': key,
            'value': {'kind': entry['kind'], 'text': text}, 'ttl_seconds': ttl}


def _normalize(skill_id, value):
    if skill_id not in MEMORY_SKILLS:
        raise StateError('unknown_skill', 404)
    if type(value) is not dict or 'scope' in value:
        raise StateError('invalid_input_fields')
    if skill_id == 'memory.set':
        norm = _validate_set(value)
        payload = {'scope': norm['scope'], 'key': norm['key'], 'value': norm['value']}
        if 'ttl_seconds' in value:
            payload['ttl_seconds'] = norm['ttl_seconds']
        return payload
    if skill_id == 'memory.get':
        _fields(value, ('project_id', 'key'), ('project_id', 'key'))
        return {'scope': project_scope(_project_id(value['project_id'])),
                'key': _text(value['key'], 'key', MAX_KEY)}
    if skill_id == 'memory.search':
        _fields(value, ('project_id', 'query', 'limit', 'offset'), ('project_id', 'query'))
        payload = {'scope': project_scope(_project_id(value['project_id'])),
                   'query': _text(value['query'], 'query', MAX_QUERY)}
        if 'limit' in value:
            payload['limit'] = _integer(value['limit'], 'limit', 1, 20)
        if 'offset' in value:
            payload['offset'] = _integer(value['offset'], 'offset', 0, 100)
        return payload
    _fields(value, ('project_id', 'all'), ('project_id', 'all'))
    if value['all'] is not True:
        raise StateError('explicit_clear_confirmation_required')
    return {'scope': project_scope(_project_id(value['project_id'])), 'all': True}


def dispatch(store, skill_id, value, actor, idem=None):
    """Route one curated project-mode memory request into the shared Store."""
    payload = _normalize(skill_id, value)
    return store.execute(skill_id, payload, actor, idem)


def _schema(properties, required):
    return {'type': 'object', 'properties': properties,
            'required': list(required), 'additionalProperties': False}


def descriptors():
    """Project-mode schemas for the existing memory IDs; the 99-ID catalog is unchanged."""
    pid = {'type': 'string', 'minLength': 1, 'maxLength': MAX_PROJECT_ID,
           'pattern': r'^[\x21-\x7e]+$'}
    key = {'type': 'string', 'minLength': 1, 'maxLength': MAX_KEY}
    entry = {'type': 'object', 'additionalProperties': False,
             'properties': {'kind': {'type': 'string', 'enum': list(PROJECT_KINDS)},
                            'text': {'type': 'string', 'minLength': 1, 'maxLength': MAX_TEXT}},
             'required': ['kind', 'text']}
    schemas = {
        'memory.set': _schema({'project_id': pid, 'consent': {'const': True},
            'key': key, 'value': entry,
            'ttl_seconds': {'type': 'integer', 'minimum': TTL_MIN, 'maximum': TTL_MAX,
                            'default': TTL_DEFAULT}},
            ('project_id', 'consent', 'key', 'value')),
        'memory.get': _schema({'project_id': pid, 'key': key}, ('project_id', 'key')),
        'memory.search': _schema({'project_id': pid,
            'query': {'type': 'string', 'minLength': 1, 'maxLength': MAX_QUERY},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 10},
            'offset': {'type': 'integer', 'minimum': 0, 'maximum': 100, 'default': 0}},
            ('project_id', 'query')),
        'memory.clear': _schema({'project_id': pid, 'all': {'const': True}},
                                ('project_id', 'all')),
    }
    descriptions = {
        'memory.set': 'Curated project decision/convention/verified_command entry; '
                      'explicit consent required, obvious secrets rejected, never auto-written.',
        'memory.get': 'Read one curated entry from the actor-owned derived project scope.',
        'memory.search': 'Literal case-insensitive search over curated project entries.',
        'memory.clear': 'Clear every curated entry of one project scope with all=true.',
    }
    return {sid: {'mode': 'project', 'description': descriptions[sid],
                  'input_schema': schema} for sid, schema in schemas.items()}
