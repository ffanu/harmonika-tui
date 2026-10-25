#!/usr/bin/env python3
"""Bounded deterministic static analyzers for Harmonika engineering operations.

Every result is derived from the redacted bundle returned by
engineering_evidence.prepare(payload). Nothing here executes code, reads a
filesystem path, opens a socket, spawns a subprocess or fetches a repository;
paths in the payload are public user-supplied identifiers only. Absence of a
finding is never evidence that code is secure or correct.
"""
import importlib
import json
import re
import sys

SCHEMA_VERSION = 1
OPERATIONS = ('code_review', 'debug', 'testing_strategy', 'documentation')
EVIDENCE_MODULE = 'engineering_evidence'
MAX_JSON_BYTES = 60 * 1024
MAX_EVIDENCE = 200
MAX_FINDINGS = 40
MAX_LINES_PER_SOURCE = 4000
MAX_TEXT = 240
MAX_REPORT_ITEMS = 30
SEVERITY_ORDER = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
METHOD = {'analysis': 'bounded_static_heuristics', 'code_executed': False, 'repository_fetched': False}

BASE_LIMITATIONS = (
    'Static pattern and structure heuristics only: no code was executed, no repository, URL or path was fetched, and no runtime behaviour was observed.',
    'Analysis is limited to the redacted payload supplied in this request; unsupplied files, configuration, dependencies and call sites were not examined.',
    'Heuristics produce both false positives and false negatives. An empty findings list means no configured pattern matched, not that the code is secure, correct or bug-free.',
    'Redaction runs before analysis, so credential-shaped values appear as markers and some original text is unavailable.',
)

FAMILY_BY_EXTENSION = {
    'py': 'indent', 'pyi': 'indent', 'rb': 'indent', 'sh': 'indent', 'bash': 'indent', 'yml': 'indent', 'yaml': 'indent',
    'js': 'brace', 'jsx': 'brace', 'mjs': 'brace', 'cjs': 'brace', 'ts': 'brace', 'tsx': 'brace', 'go': 'brace',
    'java': 'brace', 'kt': 'brace', 'kts': 'brace', 'c': 'brace', 'h': 'brace', 'cc': 'brace', 'cpp': 'brace',
    'hpp': 'brace', 'cs': 'brace', 'rs': 'brace', 'php': 'brace', 'swift': 'brace', 'scala': 'brace',
}
LANGUAGE_BY_EXTENSION = {
    'py': 'python', 'pyi': 'python', 'rb': 'ruby', 'sh': 'shell', 'bash': 'shell', 'js': 'javascript', 'mjs': 'javascript',
    'cjs': 'javascript', 'jsx': 'javascript', 'ts': 'typescript', 'tsx': 'typescript', 'go': 'go', 'java': 'java',
    'kt': 'kotlin', 'c': 'c', 'h': 'c', 'cc': 'cpp', 'cpp': 'cpp', 'hpp': 'cpp', 'cs': 'csharp', 'rs': 'rust',
    'php': 'php', 'swift': 'swift', 'scala': 'scala', 'sql': 'sql', 'yml': 'yaml', 'yaml': 'yaml', 'json': 'json',
    'md': 'markdown', 'tf': 'terraform',
}
LOG_IDENTIFIER = 'supplied.log'
COMMENT_PREFIXES = ('#', '//', '*', '/*', '--', '<!--')
TEST_PATH = re.compile(r'(?i)(^|[/_.-])(tests?|spec|specs|__tests__|e2e)([/_.-]|$)|(_test|test_|\.test|\.spec)')


def evidence_module():
    """The caller (root integration) puts engineering_evidence on sys.path."""
    module = sys.modules.get(EVIDENCE_MODULE)
    if module is not None:
        return module
    try:
        return importlib.import_module(EVIDENCE_MODULE)
    except ImportError:
        raise RuntimeError('engineering_evidence_unavailable') from None


def clip(text, limit=MAX_TEXT):
    value = ' '.join(str(text).split())
    return value if len(value) <= limit else value[:limit - 1] + '…'


def is_comment(text):
    stripped = text.strip()
    return stripped.startswith(COMMENT_PREFIXES) or stripped.startswith('"""') or stripped.startswith("'''")


def extension(path):
    name = str(path).rsplit('/', 1)[-1]
    return name.rsplit('.', 1)[-1].lower() if '.' in name else ''


def language_of(path, declared=''):
    declared = (declared or '').strip().lower()
    return declared or LANGUAGE_BY_EXTENSION.get(extension(path), '')


def family_of(path, language=''):
    family = FAMILY_BY_EXTENSION.get(extension(path))
    if family:
        return family
    if language in ('javascript', 'typescript', 'go', 'java', 'c', 'cpp', 'csharp', 'rust', 'php', 'swift', 'kotlin', 'scala'):
        return 'brace'
    return 'indent'


class Ledger:
    """Evidence register: every citation resolves to an item in the output."""

    def __init__(self, upstream):
        self.upstream = []
        self.index = {}
        self.used_ids = set()
        self.cited = []
        self.cited_ids = set()
        self.minted = 0
        self.capped = False
        for item in upstream if isinstance(upstream, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                line = int(item.get('line') or 0)
            except (TypeError, ValueError):
                line = 0
            entry = {'id': str(item.get('id') or ''), 'source': str(item.get('source') or ''),
                     'path': str(item.get('path') or ''), 'line': line, 'text': clip(item.get('text') or '')}
            if not entry['id'] or entry['id'] in self.used_ids:
                continue
            self.used_ids.add(entry['id'])
            self.upstream.append(entry)
            self.index.setdefault((entry['source'], entry['path'], entry['line']), entry)

    def cite(self, source, path, line, text):
        """Return the evidence id for one redacted line, minting it if needed."""
        try:
            line = int(line)
        except (TypeError, ValueError):
            line = 0
        key = (str(source), str(path), line)
        entry = self.index.get(key)
        if entry is None:
            if len(self.cited) >= MAX_EVIDENCE:
                self.capped = True
                return ''
            self.minted += 1
            identifier = 'analyzer-%d' % self.minted
            while identifier in self.used_ids:
                self.minted += 1
                identifier = 'analyzer-%d' % self.minted
            self.used_ids.add(identifier)
            entry = {'id': identifier, 'source': key[0], 'path': key[1], 'line': line, 'text': clip(text)}
            self.index[key] = entry
        if entry['id'] not in self.cited_ids:
            if len(self.cited) >= MAX_EVIDENCE:
                self.capped = True
                return ''
            self.cited.append(entry)
            self.cited_ids.add(entry['id'])
        return entry['id']

    def records(self):
        records = list(self.cited)
        for entry in self.upstream:
            if len(records) >= MAX_EVIDENCE:
                self.capped = True
                break
            if entry['id'] not in self.cited_ids:
                records.append(entry)
        return records


class Unit:
    """One analysable text region with original line numbers preserved."""

    def __init__(self, source, path, text='', language='', lines=None, partial=False):
        self.source = source
        self.path = path
        self.language = language or language_of(path)
        self.family = family_of(path, self.language)
        self.partial = partial
        self.truncated = False
        if lines is None:
            lines = [(number, value, True) for number, value in enumerate(str(text).splitlines(), 1)]
        if len(lines) > MAX_LINES_PER_SOURCE:
            lines = lines[:MAX_LINES_PER_SOURCE]
            self.truncated = True
        self.lines = lines
        self.is_test = bool(TEST_PATH.search(path))
        self.body = '\n'.join(value for _, value, _ in self.lines)

HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def parse_diff(diff):
    """Rebuild the post-change side of a unified diff with new-file line numbers.

    Returns a list of units whose context lines are analysable for structure but
    only added lines are reportable, because only they belong to the change.
    """
    units = []
    path = ''
    lines = []
    number = 0
    inside = False
    for raw in str(diff).splitlines():
        if raw.startswith('+++ '):
            if path and lines:
                units.append(Unit('diff', path, lines=lines, partial=True))
            candidate = raw[4:].strip().split('\t', 1)[0]
            if candidate.startswith(('a/', 'b/', 'i/', 'w/', 'c/')):
                candidate = candidate[2:]
            path = '' if candidate in ('/dev/null', '') else candidate
            lines = []
            inside = False
            continue
        if raw.startswith('diff --git') or raw.startswith('--- '):
            inside = False
            continue
        match = HUNK.match(raw)
        if match:
            number = int(match.group(1))
            inside = True
            continue
        if not inside or not path:
            continue
        if raw.startswith('+'):
            lines.append((number, raw[1:], True))
            number += 1
        elif raw.startswith('-') or raw.startswith('\\'):
            continue
        elif raw.startswith(' ') or raw == '':
            lines.append((number, raw[1:] if raw else '', False))
            number += 1
    if path and lines:
        units.append(Unit('diff', path, lines=lines, partial=True))
    return units


def file_units(files):
    """Build units from prepare()'s redacted file list, skipping malformed rows."""
    units, warnings = [], []
    for item in files if isinstance(files, list) else []:
        if not isinstance(item, dict):
            warnings.append('Skipped a file entry that was not an object.')
            continue
        path = str(item.get('path') or '').strip()
        content = item.get('content')
        if not path or not isinstance(content, str):
            warnings.append('Skipped a file entry without a path and string content.')
            continue
        units.append(Unit('file', path, text=content, language=str(item.get('language') or '')))
    return units, warnings


def loop_spans(unit):
    """Line numbers at which an enclosing loop starts, per analysed line.

    Indentation for Python-like families, brace depth for C-like families. The
    result is a list parallel to unit.lines holding the loop header line number
    or 0 when the line is not inside a loop in the supplied text.
    """
    spans = []
    if unit.family == 'brace':
        depth = 0
        stack = []
        opener = re.compile(r'(?<![\w$.])(for|while|foreach)\s*[\(<]|\.\s*(forEach|map|flatMap|filter)\s*\(')
        for _, text, _ in unit.lines:
            spans.append(stack[-1][1] if stack else 0)
            if is_comment(text):
                continue
            opens = text.count('{') + text.count('(')
            closes = text.count('}') + text.count(')')
            if opener.search(text):
                stack.append((depth, unit.lines[len(spans) - 1][0]))
            depth += opens - closes
            while stack and depth <= stack[-1][0]:
                stack.pop()
        return spans
    stack = []
    opener = re.compile(r'^\s*(?:for\b|while\b|\w+\.each\b|.*\.each\s+do\b)')
    for number, text, _ in unit.lines:
        if text.strip():
            indent = len(text) - len(text.lstrip())
            while stack and indent <= stack[-1][0]:
                stack.pop()
        spans.append(stack[-1][1] if stack else 0)
        if text.strip() and not is_comment(text) and opener.match(text):
            stack.append((len(text) - len(text.lstrip()), number))
    return spans


SQL_STATEMENT = re.compile(r'(?i)\b(select\s+[\w*`"\[\].,\s]+\s+from\s|insert\s+into\s|update\s+[\w.`"\[\]]+\s+set\s|delete\s+from\s|drop\s+table\s|alter\s+table\s)')
FSTRING = re.compile(r'''(?i)\bf(['"])''')
FORMAT_PERCENT = re.compile(r'''['"]\s*%\s*[\(\w]''')
CONCAT = re.compile(r'''(['"]\s*\+\s*[\w(]|[\w)]\s*\+\s*['"])''')
TEMPLATE = re.compile(r'(\$\{|#\{)')
USER_INPUT = re.compile(r'(?i)(\breq\b|\brequest\b|\bparams\b|\bquery_params\b|\bbody\b|\bform\b|\bargs\b|\bargv\b|\buser_input\b|\bpayload\b|\buntrusted\b|\$_(?:GET|POST|REQUEST)|\bGET\[|\bPOST\[|\bgetParameter\(|\bc\.Query\(|\bmux\.Vars\()')
SANITIZER = re.compile(r'(?i)(basename|abspath|realpath|normpath|secure_filename|safe_join|\.resolve\(|startswith\(|escape\(|sanit|allowlist|whitelist|validate|is_safe_url)')
SECRET_WORD = re.compile(r'(?i)(pass(?:word|wd)?|secret|token|api[_-]?key|credential|signing|session[_-]?key|private[_-]?key)')
ENV_LOOKUP = re.compile(r'(?i)(os\.environ|os\.getenv|\bgetenv\b|process\.env|secretsmanager|secret_manager|vault|keyring|keychain|param_store|dotenv|config\.get\(|settings\.get\(|viper\.get)')
REDACTION_MARKER = re.compile(r'(?i)(redact|\*{4,}|x{8,})')
PLACEHOLDER_VALUE = re.compile(r'''(?i)^(|\s*|x+|y+|\*+|\.+|change[-_ ]?me|your[-_\w]*|my[-_\w]*|<[^>]*>|\{\{?[^}]*\}?\}|\$\{?[\w.]+\}?|%s|none|null|nil|true|false|todo|tbd|example|sample|test|testing|dummy|fake|foo|bar|baz|placeholder|localhost|password|secret|token|key|\d{1,4})$''')
CREDENTIAL_NAME = r'''\w*(?:pass(?:word|wd|phrase)|secret|token|api[_-]?key|apikey|access[_-]?key|client[_-]?secret|private[_-]?key|credentials?|authorization)\w*'''
CREDENTIAL_REDACTED = re.compile(r'''(?i)\b(?:%s)\w*["']?\s*(?:=|:=|:|=>)\s*\[REDACTED''' % CREDENTIAL_NAME)
CREDENTIAL_ASSIGN = re.compile(r'''(?i)\b(%s)\s*(?:=|:=|:|=>)\s*(['"])(?P<value>.*?)\2''' % CREDENTIAL_NAME)


DIVISION = re.compile(r'(?<=[\w\)\]])\s*/\s*(len\s*\(|\w*(?:count|total|size|length|num)\w*\b)')


def in_string(text, index):
    """True when the character at index sits inside a simple quoted literal."""
    quote = None
    for position in range(min(index, len(text))):
        character = text[position]
        if quote:
            if character == quote and (position == 0 or text[position - 1] != '\\'):
                quote = None
        elif character in '"\'`':
            quote = character
    return quote is not None


def division_guard(text):
    for match in DIVISION.finditer(text):
        if not in_string(text, match.start(1)):
            return 'A count-like value is used as a divisor and no zero guard is visible on this line.'
    return None


def credential_guard(text):
    match = CREDENTIAL_ASSIGN.search(text)
    if not match or ENV_LOOKUP.search(text):
        return None
    value = match.group('value')
    if REDACTION_MARKER.search(value):
        return None
    if PLACEHOLDER_VALUE.match(value) or len(value) < 6:
        return None
    if value.startswith(('/', './', 'http://localhost')) or ' ' in value.strip():
        return None
    return 'A quoted literal is assigned directly to a credential-named identifier; the value is not read from an environment variable or secret store on this line.'


def sql_guard(text):
    if not SQL_STATEMENT.search(text):
        return None
    interpolated = ((FSTRING.search(text) and '{' in text) or '.format(' in text
                    or FORMAT_PERCENT.search(text) or CONCAT.search(text) or TEMPLATE.search(text))
    if not interpolated:
        return None
    return 'The SQL text is assembled by interpolation or concatenation rather than passed as a parameterised statement.'


def path_guard(text):
    if not USER_INPUT.search(text) or SANITIZER.search(text):
        return None
    return 'A request-derived value reaches a filesystem call on this line with no normalisation or allowlist check visible here.'


def request_guard(text):
    if not USER_INPUT.search(text) or SANITIZER.search(text):
        return None
    return 'A request-derived value is used as the target of an outbound request on this line with no allowlist check visible here.'


def xss_guard(text):
    if re.search(r'''(innerHTML|outerHTML)\s*=\s*(['"][^'"]*['"]\s*;?\s*$)''', text):
        return None
    return 'Markup is assigned from a non-literal value, so any untrusted substring is rendered as HTML rather than text.'


def weak_hash_guard(text):
    if not SECRET_WORD.search(text):
        return None
    return 'A fast general-purpose digest is applied to credential-like material on this line.'


def random_guard(text):
    if not SECRET_WORD.search(text):
        return None
    return 'A non-cryptographic pseudo-random generator produces security-relevant material on this line.'


def rule(identifier, dimension, severity, kind, pattern, title, recommendation, guard=None, flags=0):
    return {'rule': identifier, 'dimension': dimension, 'severity': severity, 'kind': kind,
            'pattern': re.compile(pattern, flags), 'title': title, 'recommendation': recommendation, 'guard': guard}


LINE_RULES = (
    rule('sql_string_interpolation', 'security', 'high', 'observation', r'(?i)(select|insert|update|delete|drop|alter)\s',
         'SQL statement built by string interpolation',
         'Pass values as bound parameters through the driver or ORM instead of embedding them in the statement text; keep identifiers on an allowlist.',
         sql_guard),
    rule('hardcoded_credential', 'security', 'high', 'observation', r'''(?i)(pass|secret|token|key|credential|auth)\w*\s*(?:=|:=|:|=>)\s*['"]''',
         'Credential-shaped literal assigned in source',
         'Load the value from the environment or a secret store at runtime, rotate the exposed value, and keep it out of version control.',
         credential_guard),
    rule('credential_assignment_redacted', 'security', 'low', 'hypothesis', r'''(?i)(?:=|:)\s*\[REDACTED''',
         'Credential-shaped assignment was redacted before analysis',
         'Check the original line: if the value is a literal, move it to the environment or a secret store and rotate it. If it already reads from one, no change is needed.',
         lambda text: ('Preparation replaced the assigned value with a redaction marker, so this line assigned something credential-shaped. '
                       'The marker hides whether the original was a literal or an environment lookup, so this needs confirmation in the original source.')
                      if CREDENTIAL_REDACTED.search(text) else None),
    rule('tls_verification_disabled', 'security', 'high', 'observation',
         r'(?i)(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|CURLOPT_SSL_VERIFYPEER\s*,\s*(?:0|false)|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*.?0)',
         'Transport certificate verification disabled',
         'Keep verification enabled and trust the correct certificate authority bundle instead; if a private CA is needed, configure its bundle explicitly.'),
    rule('unsafe_deserialization', 'security', 'high', 'observation',
         r'(pickle\.loads?\(|cPickle\.loads?\(|marshal\.loads\(|yaml\.load\((?![^)]*(?:Safe|safe_load))|yaml\.unsafe_load\(|ObjectInputStream\(|\bunserialize\(|jsonpickle\.decode\()',
         'Deserialisation of untrusted-capable format',
         'Use a data-only format (JSON) or yaml.safe_load; never deserialise object graphs from input you do not control.'),
    rule('command_injection', 'security', 'high', 'observation',
         r'(os\.system\(|subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True|child_process\.exec(?:Sync)?\(|\bos/exec\.Command\("(?:sh|bash)"|\bpopen\()',
         'Shell invocation on a path that can carry input',
         'Call the binary with an argument list and shell=False, or validate against a strict allowlist before building any shell string.',
         lambda text: 'A shell is spawned on this line; shell metacharacters in any interpolated value would be interpreted.' if (USER_INPUT.search(text) or FSTRING.search(text) or TEMPLATE.search(text) or CONCAT.search(text) or '.format(' in text) else None),
    rule('shell_invocation', 'security', 'medium', 'observation',
         r'(subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True|os\.system\(|child_process\.exec(?:Sync)?\()',
         'Command executed through a shell',
         'Prefer an argument list without a shell so quoting and metacharacters cannot change the command.'),
    rule('dynamic_code_evaluation', 'security', 'high', 'observation',
         r'''(?<![\w.])(eval\(|new Function\(|setTimeout\(\s*["']|vm\.runInNewContext\(|exec\(\s*[a-z_]\w*\s*[,)])''',
         'Dynamic evaluation of a code string',
         'Replace dynamic evaluation with explicit parsing or a dispatch table; evaluated strings make input a code path.'),
    rule('path_traversal_risk', 'security', 'medium', 'hypothesis',
         r'(open\(|io\.open\(|readFileSync?\(|fs\.createReadStream\(|send_file\(|sendFile\(|os\.path\.join\(|filepath\.Join\()',
         'Request-derived value used to build a filesystem path',
         'Resolve the path and confirm it stays inside an allowed root, or map the input to a key in an allowlist instead of a path fragment.',
         path_guard),
    rule('ssrf_risk', 'security', 'medium', 'hypothesis',
         r'(requests\.(?:get|post|put|delete|head|request)\(|urlopen\(|httpx\.(?:get|post|request)\(|axios\.(?:get|post|request)\(|fetch\(|http\.Get\(|HttpClient\w*\.(?:get|send))',
         'Request-derived value used as an outbound request target',
         'Validate the destination against an allowlist of hosts and schemes, block link-local and private ranges, and disable redirects you do not need.',
         request_guard),
    rule('cross_site_scripting_risk', 'security', 'high', 'observation',
         r'(innerHTML\s*=|outerHTML\s*=|dangerouslySetInnerHTML|document\.write\(|render_template_string\(|\|\s*safe\b|v-html|Markup\()',
         'Unescaped value rendered as markup',
         'Render text through the framework escaping path, or sanitise with a vetted HTML sanitiser before assigning markup.',
         xss_guard),
    rule('csrf_protection_disabled', 'security', 'medium', 'observation',
         r'(?i)(csrf_exempt|csrf\s*:\s*false|csrfProtection\s*:\s*false|@csrf\.exempt|WTF_CSRF_ENABLED\s*=\s*False)',
         'CSRF protection disabled for a handler',
         'Keep CSRF protection on for cookie-authenticated state changes; if the endpoint is token-authenticated, document that and reject cookie auth.'),
    rule('debug_mode_enabled', 'security', 'low', 'observation',
         r'(?i)(debug\s*=\s*True|DEBUG\s*:\s*true|app\.run\([^)]*debug\s*=\s*True|FLASK_DEBUG\s*=\s*1)',
         'Debug mode enabled in source',
         'Drive debug mode from configuration per environment; debug handlers expose stack traces and can allow code execution in production.'),
    rule('weak_hash_for_secret', 'security', 'medium', 'hypothesis',
         r'''(hashlib\.(?:md5|sha1)\(|crypto\.createHash\(\s*["'](?:md5|sha1)|MessageDigest\.getInstance\(\s*"(?:MD5|SHA-1)"|md5\()''',
         'Weak digest applied to credential-like material',
         'Use argon2id or bcrypt for passwords and HMAC-SHA256 or better for integrity; MD5 and SHA-1 are unsuitable for either.',
         weak_hash_guard),
    rule('insecure_randomness', 'security', 'medium', 'hypothesis',
         r'(random\.(?:random|randint|choice|randrange|shuffle)\(|Math\.random\(|rand\(\))',
         'Non-cryptographic randomness used for security material',
         'Use secrets, os.urandom, crypto.randomBytes or an equivalent CSPRNG for tokens, keys, salts and identifiers.',
         random_guard),
    rule('mutable_default_argument', 'correctness', 'medium', 'observation',
         r'def\s+\w+\s*\([^)]*=\s*(\[\]|\{\}|set\(\)|dict\(\)|list\(\))',
         'Mutable default argument shared across calls',
         'Default to None and build the container inside the function, otherwise state leaks between calls.'),
    rule('bare_except', 'correctness', 'medium', 'observation', r'^\s*except\s*:',
         'Bare except catches every exception',
         'Catch the specific exception types you can handle so KeyboardInterrupt, SystemExit and genuine defects are not swallowed.'),
    rule('off_by_one_risk', 'correctness', 'medium', 'hypothesis',
         r'(range\s*\(\s*(?:0\s*,\s*)?len\s*\([^)]*\)\s*\+\s*1|<=\s*len\s*\(|<=\s*\w+\.length\b|<=\s*\w+\.size\(\)|\[\s*len\s*\([^)]*\)\s*\])',
         'Loop or index bound includes the length value',
         'Compare with < length, or index length - 1; verify the intended inclusive bound with a boundary test.'),
    rule('unchecked_division', 'correctness', 'low', 'hypothesis', DIVISION.pattern,
         'Division by a computed count without a visible zero guard',
         'Guard the zero case explicitly and decide the intended result for an empty collection.',
         division_guard),
    rule('assert_used_for_validation', 'correctness', 'low', 'observation', r'^\s*assert\s+(?!.*#\s*noqa)',
         'assert used outside a test module',
         'Raise an explicit exception instead; assertions are removed when Python runs with -O, which would drop the check.'),
    rule('float_money', 'correctness', 'low', 'hypothesis',
         r'(?i)\b(float|parseFloat)\s*\(\s*[^)]*(amount|price|total|balance|salary|payment|cost|fee)',
         'Monetary value converted to binary floating point',
         'Represent money as integer minor units or a decimal type so rounding is exact and auditable.'),
    rule('todo_marker', 'maintainability', 'low', 'observation', r'(?i)\b(TODO|FIXME|XXX|HACK)\b[: ]',
         'Unresolved work marker in the reviewed text',
         'Resolve the item or link it to a tracked ticket so the remaining work is visible outside the source.'),
    rule('missing_timeout', 'performance', 'medium', 'hypothesis',
         r'(requests\.(?:get|post|put|delete|head)\(|urlopen\(|http\.Get\(|httpx\.(?:get|post)\()',
         'Outbound request without an explicit timeout',
         'Set an explicit connect and read timeout so a slow dependency cannot hold the caller and its resources indefinitely.',
         lambda text: None if re.search(r'(?i)timeout', text) else 'No timeout argument appears on this call, so the library default (often unbounded) applies.'),
    rule('unbounded_result_set', 'performance', 'low', 'hypothesis',
         r'''(?i)(\.all\(\)|findAll\(\s*\)|select\s+\*\s+from\s+\w+\s*(?:;|["'])|\.find\(\s*\{?\s*\}?\s*\))''',
         'Query appears to fetch an unbounded result set',
         'Apply an explicit limit and pagination so result size cannot grow with the table.',
         lambda text: None if re.search(r'(?i)(limit|paginate|\.first\(|\.slice\(|top\s+\d+|fetch\s+first)', text) else 'No limit, pagination or slicing appears on this line.'),
)


QUERY_CALL = re.compile(r'''(?i)(\.(?:execute|executemany|query|fetchone|fetchall|get|filter|find|findOne|findById|aggregate|count|exists|save|insert|update|delete|first|all)\s*\(|\bselect\s+[\w*`"\[\].,\s]+\s+from\s|objects\.|session\.query\(|db\.|repository\.|prisma\.|knex\(|collection\(|\.populate\()''')
NETWORK_CALL = re.compile(r'''(?i)(requests\.(?:get|post|put|delete|head|request)\(|urlopen\(|httpx\.|axios\.|\bfetch\(|http\.(?:Get|Post)\(|HttpClient|\.send\(|boto3\.client\(|s3\.)''')
ORM_HINT = re.compile(r'''(?i)(objects\.|session\.query\(|\.query\(|prisma\.|sequelize|mongoose|knex|\.execute\(|repository\.)''')
AWAIT_CALL = re.compile(r'\bawait\s+\w')
EXCEPT_OPEN = re.compile(r'^\s*(except\b[^:]*:|catch\s*\([^)]*\)\s*\{?)\s*$')
SWALLOW = re.compile(r'^\s*(pass|continue|return(\s+(None|null|nil|\{\}|\[\]|false|true))?\s*;?|\}?\s*)$')
EXISTENCE_CHECK = re.compile(r'''(?i)(if\s+not\s+(?:os\.path\.)?(?:exists|isfile|isdir)\(|if\s*\(\s*!?\s*fs\.existsSync\(|if\s+not\s+\w+\.(?:get|exists)\(|if\s+\w+\s+(?:is\s+None|==\s*None|not\s+in\b))''')
ACT_AFTER_CHECK = re.compile(r'''(?i)(open\(\s*[^)]*['"]?[wax]|mkdir\(|makedirs\(|writeFile|createWriteStream|\.set\(|\.create\(|\.save\(|insert\(|rename\()''')
RESOURCE_OPEN = re.compile(r'''(?i)(\w+\s*=\s*(?:open\(|socket\.socket\(|psycopg2\.connect\(|sqlite3\.connect\(|pymysql\.connect\(|urlopen\(|net\.Dial\(|os\.Open\())''')
CONCAT_ASSIGN = re.compile(r'''^\s*\w+\s*\+=\s*(['"]|f['"]|\w+\s*\+\s*['"])''')
FUNCTION_START = re.compile(r'''^\s*(?:async\s+)?(?:def|function|func|fn)\s+\w+|^\s*(?:export\s+)?(?:async\s+)?function\s+\w+|^\s*\w[\w<>,\[\] ]*\s+\w+\s*\([^;]*\)\s*\{\s*$''')


def context_hits(unit):
    """Multi-line and loop-scoped checks that a single-line regex cannot express."""
    hits = []
    spans = loop_spans(unit)
    lines = unit.lines
    count = len(lines)
    for index in range(count):
        number, text, reportable = lines[index]
        if not reportable or is_comment(text) or not text.strip():
            continue
        inline_loop = bool(re.match(r'^\s*(for|while)\b.*:\s*\S', text)) or bool(re.search(r'\bfor\s*\(.*\)\s*\S', text))
        loop_line = spans[index] or (number if inline_loop else 0)
        if loop_line and QUERY_CALL.search(text) and ORM_HINT.search(text):
            hits.append(('n_plus_one_query', number, text,
                         'A data-access call runs inside the loop that starts at line %d, so the query count grows with the collection.' % loop_line))
        elif loop_line and NETWORK_CALL.search(text):
            hits.append(('network_call_in_loop', number, text,
                         'A network call runs inside the loop that starts at line %d, so latency multiplies with the collection size.' % loop_line))
        if loop_line and AWAIT_CALL.search(text) and (NETWORK_CALL.search(text) or QUERY_CALL.search(text)):
            hits.append(('sequential_await_in_loop', number, text,
                         'Each iteration of the loop at line %d awaits before the next starts, so the calls run serially.' % loop_line))
        if loop_line and CONCAT_ASSIGN.match(text):
            hits.append(('string_concatenation_in_loop', number, text,
                         'A string is rebuilt on each iteration of the loop at line %d, which is quadratic in the accumulated length.' % loop_line))
        if loop_line and spans[index] and re.search(r'\bin\s+(?:list\(|\[|\w+_list\b|\w+s\b)', text) and not re.search(r'(?i)(set\(|dict\(|\{\}|\.keys\(\)|range\()', text):
            hits.append(('nested_membership_scan', number, text,
                         'A membership test over a sequence runs inside the loop at line %d, giving quadratic comparisons; a set or dict lookup is constant time.' % loop_line))
        if EXCEPT_OPEN.match(text):
            following = [lines[j][1] for j in range(index + 1, min(index + 3, count))]
            body = [value for value in following if value.strip()]
            if body and SWALLOW.match(body[0]) and not any(re.search(r'(?i)(log|raise|throw|report|metric|span|print)', value) for value in body[:1]):
                hits.append(('swallowed_exception', number, text,
                             'The handler body on line %d discards the error without logging or re-raising, so the failure becomes invisible.' % (number + 1)))
        if EXISTENCE_CHECK.search(text):
            for offset in range(1, 4):
                if index + offset >= count:
                    break
                follow = lines[index + offset][1]
                if follow.strip() and ACT_AFTER_CHECK.search(follow):
                    hits.append(('check_then_act_race', number, text,
                                 'The state checked here is acted on at line %d; between the two steps another process or request can change it.' % lines[index + offset][0]))
                    break
    opens = {}
    for number, text, reportable in lines:
        match = RESOURCE_OPEN.search(text)
        if match and 'with ' not in text and 'using ' not in text and reportable:
            opens[number] = text
    if opens and not re.search(r'(?i)(\.close\(|defer\s+\w+\.Close\(|finally\s*:|try\s*\{)', unit.body):
        number, text = sorted(opens.items())[0]
        hits.append(('unreleased_resource', number, text,
                     'A handle or connection is opened outside a context manager and no close or finally block appears in the supplied text.'))
    starts = [index for index in range(count) if FUNCTION_START.match(lines[index][1])]
    for position, index in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else count
        length = lines[end - 1][0] - lines[index][0] + 1
        if length > 80 and lines[index][2]:
            hits.append(('long_function', lines[index][0], lines[index][1],
                         'This definition spans about %d lines before the next definition, which makes the unit hard to test in isolation.' % length))
    return hits


CONTEXT_RULE_META = {
    'n_plus_one_query': ('performance', 'high', 'observation', 'Data-access call inside a loop (N+1 pattern)',
                         'Fetch the rows in one query with a join, an IN filter or an eager-load, then map them in memory.'),
    'network_call_in_loop': ('performance', 'medium', 'observation', 'Network call inside a loop',
                             'Batch the calls, or run them concurrently with a bounded worker pool and per-call timeout.'),
    'sequential_await_in_loop': ('performance', 'medium', 'observation', 'Awaited call serialises the loop',
                                 'Collect the awaitables and gather them with a concurrency limit when the calls are independent.'),
    'string_concatenation_in_loop': ('performance', 'low', 'observation', 'String rebuilt on every iteration',
                                     'Append to a list and join once, or use a string builder.'),
    'nested_membership_scan': ('performance', 'medium', 'hypothesis', 'Linear membership test inside a loop',
                               'Build a set or dict outside the loop and test membership against it.'),
    'swallowed_exception': ('correctness', 'medium', 'observation', 'Exception discarded without logging or re-raise',
                            'Log the error with context and re-raise, or handle it explicitly and record why it is safe to continue.'),
    'check_then_act_race': ('correctness', 'medium', 'hypothesis', 'Check-then-act sequence open to a race',
                            'Make the operation atomic: use exclusive creation, a unique constraint, a transaction or a lock instead of checking first.'),
    'unreleased_resource': ('performance', 'medium', 'hypothesis', 'Resource opened without a visible release',
                            'Open it in a context manager (with / defer / try-finally) so the handle is released on every path.'),
    'long_function': ('maintainability', 'low', 'observation', 'Long function body',
                      'Split the unit along its responsibilities so each part can be tested directly.'),
}

POSITIVE_CHECKS = (
    ('parameterised_query', re.compile(r'''(?i)(execute\(\s*['"][^'"]*(?:%s|\?|:\w+|\$\d)[^'"]*['"]\s*,|cursor\.execute\([^)]*,\s*[\(\[]|\.prepare\(|bindParams|\?\s*['"]\s*,\s*\[)'''),
     'Query values are bound as parameters rather than interpolated into SQL.'),
    ('context_managed_resource', re.compile(r'''^\s*(with\s+[\w.]+\(|async with\s+|using\s*\(|defer\s+\w+\.Close\()'''),
     'A resource is acquired in a construct that releases it on every path.'),
    ('specific_exception_handling', re.compile(r'''^\s*(except\s+\w*(?:Error|Exception|Timeout|Exit)\w*(\s*\([^)]*\))?\s+as\s+\w+\s*:|catch\s*\(\s*\w*Error)'''),
     'A specific exception type is caught and bound, rather than every exception.'),
    ('explicit_input_validation', re.compile(r'''(?i)(raise\s+(?:ValueError|TypeError|ValidationError)\(|throw new (?:TypeError|RangeError|ValidationError)\(|\.validate\(|pydantic|zod\.|schema\.(?:load|parse)\()'''),
     'Input is validated explicitly with a typed failure.'),
    ('secret_from_environment', re.compile(r'''(?i)(os\.environ(?:\.get)?[\(\[]['"][^'"]*(?:KEY|TOKEN|SECRET|PASSWORD)|os\.getenv\(['"][^'"]*(?:KEY|TOKEN|SECRET|PASSWORD)|process\.env\.[A-Z_]*(?:KEY|TOKEN|SECRET|PASSWORD))'''),
     'A credential is read from the environment instead of being embedded in source.'),
    ('explicit_timeout', re.compile(r'''(?i)(timeout\s*=\s*\d|timeout:\s*\d|WithTimeout\(|setTimeout\(\s*\w+\s*,\s*\d)'''),
     'An explicit timeout bounds a call that can block.'),
    ('bounded_query', re.compile(r'''(?i)(\blimit\s+\d+|\.limit\(\s*\d|paginate\(|fetch\s+first\s+\d+)'''),
     'A query result is explicitly bounded.'),
    ('type_annotations', re.compile(r'''(def\s+\w+\s*\([^)]*:\s*[\w\[\]\.]+|\)\s*->\s*[\w\[\]\.]+\s*:|:\s*(?:string|number|boolean)\b)'''),
     'Signatures carry type annotations, so mismatches can be caught statically.'),
)


def positive_hits(unit):
    hits = []
    seen = set()
    for number, text, reportable in unit.lines:
        if not reportable or is_comment(text):
            continue
        for identifier, pattern, statement in POSITIVE_CHECKS:
            if identifier in seen:
                continue
            if pattern.search(text):
                seen.add(identifier)
                hits.append((identifier, number, text, statement))
    return hits


def duplicate_blocks(units, window=6):
    """Exact duplicate blocks of significant lines across the supplied text."""
    seen, duplicates = {}, []
    for unit in units:
        significant = [(number, text, reportable) for number, text, reportable in unit.lines
                       if text.strip() and not is_comment(text)]
        for index in range(len(significant) - window + 1):
            block = significant[index:index + window]
            normalised = tuple(' '.join(text.split()) for _, text, _ in block)
            if len(set(normalised)) < 4 or all(re.match(r'^\s*(import|from|#include|using|require\()', text) for text in normalised):
                continue
            key = hash(normalised)
            first = seen.get(key)
            if first is None:
                seen[key] = (unit, block[0][0])
            elif any(reportable for _, _, reportable in block):
                duplicates.append((unit, block[0][0], block[0][1], first[0].path, first[1]))
                if len(duplicates) >= 5:
                    return duplicates
    return duplicates


DEFINITION_PATTERNS = (
    ('function', re.compile(r'^\s*(?:async\s+)?def\s+(?P<name>\w+)\s*\((?P<params>[^)]*)')),
    ('class', re.compile(r'^\s*class\s+(?P<name>\w+)\s*[\(:]')),
    ('function', re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>\w+)\s*\((?P<params>[^)]*)')),
    ('function', re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s*)?\((?P<params>[^)]*)\)\s*=>')),
    ('class', re.compile(r'^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>\w+)\b')),
    ('function', re.compile(r'^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)\s*\((?P<params>[^)]*)')),
    ('function', re.compile(r'^\s*(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\],. ]+\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*\{')),
)
ROUTE_PATTERNS = (
    re.compile(r'''(?i)@(?:app|api|router|bp|blueprint|\w+)\.(?P<method>get|post|put|patch|delete|head|options|route)\(\s*['"](?P<path>[^'"]+)['"](?P<rest>[^)]*)'''),
    re.compile(r'''(?i)\b(?:app|router|api|server|r|mux)\.(?P<method>get|post|put|patch|delete|head|options|all|use)\(\s*['"](?P<path>/[^'"]*)['"]'''),
    re.compile(r'''(?i)(?:http|mux|r)\.Handle(?:Func)?\(\s*['"](?P<path>/[^'"]*)['"]'''),
    re.compile(r'''(?i)@(?:Get|Post|Put|Patch|Delete|RequestMapping)\w*\(\s*(?:value\s*=\s*)?['"](?P<path>/[^'"]*)['"]'''),
)
METHODS_ARGUMENT = re.compile(r'''(?i)methods\s*=\s*[\[\(]([^\]\)]*)[\]\)]''')
ENV_PATTERNS = (
    re.compile(r'''os\.environ(?:\.get)?\(\s*['"](?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]'''),
    re.compile(r'''os\.environ\[\s*['"](?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]'''),
    re.compile(r'''os\.getenv\(\s*['"](?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]'''),
    re.compile(r'''process\.env\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)'''),
    re.compile(r'''process\.env\[\s*['"](?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]'''),
    re.compile(r'''os\.Getenv\(\s*"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"'''),
    re.compile(r'''ENV\[\s*['"](?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]'''),
)
RAISE_PATTERN = re.compile(r'''(?i)(?:raise\s+(?P<type>\w+)\s*\(\s*(?P<message>['"][^'"]{0,120}['"])?|throw\s+new\s+(?P<jstype>\w+)\s*\(\s*(?P<jsmessage>['"][^'"]{0,120}['"])?|errors\.New\(\s*(?P<gomessage>"[^"]{0,120}")|panic\(\s*(?P<panicmessage>"[^"]{0,120}")?)''')
BRANCH_PATTERN = re.compile(r'''^\s*(if|elif|else\s+if|case|when|match|switch)\b|\?\s*[\w'"]+\s*:''')
CLI_PATTERNS = (
    ('subcommand', re.compile(r'''add_parser\(\s*['"](?P<name>[\w.-]+)['"]''')),
    ('option', re.compile(r'''add_argument\(\s*['"](?P<name>--?[\w-]+)['"]''')),
    ('subcommand', re.compile(r'''\.command\(\s*['"](?P<name>[\w.-]+)['"]''')),
)
ENTRYPOINT = re.compile(r'''(if\s+__name__\s*==\s*['"]__main__['"]|func\s+main\s*\(|public\s+static\s+void\s+main\s*\(|^\s*#!/)''')
DOCSTRING_START = re.compile(r'''^\s*(?:r|u|b)?("""|\'\'\')''')
EXTERNAL_IMPORT = re.compile(r'''(?i)^\s*(?:import|from|require|use)\b.*\b(requests|httpx|urllib|boto3|psycopg2|pymysql|sqlalchemy|redis|kafka|pika|aiohttp|axios|mongoose|prisma|knex|net/http|database/sql|socket|smtplib|paramiko)\b''')


def structure(unit):
    """Deterministic structural inventory of one unit: no inference about behaviour."""
    definitions, routes, env_vars, errors, branches, entrypoints, dependencies, cli = [], [], [], [], 0, [], [], []
    lines = unit.lines
    for index, (number, text, reportable) in enumerate(lines):
        if not text.strip():
            continue
        if not is_comment(text):
            for kind, pattern in DEFINITION_PATTERNS:
                match = pattern.match(text)
                if match:
                    name = match.group('name')
                    params = (match.groupdict().get('params') or '').strip()
                    documented = False
                    for offset in range(1, 3):
                        if index + offset < len(lines) and lines[index + offset][1].strip():
                            documented = bool(DOCSTRING_START.match(lines[index + offset][1])) or lines[index + offset][1].strip().startswith('//')
                            break
                    definitions.append({'kind': kind, 'name': name, 'path': unit.path, 'line': number,
                                        'parameters': [p.strip().split(':')[0].split('=')[0].strip() for p in params.split(',') if p.strip() and p.strip() not in ('self', 'cls')][:8],
                                        'exported': not name.startswith('_'), 'documented': documented, 'text': text})
                    break
            for pattern in ROUTE_PATTERNS:
                for match in pattern.finditer(text):
                    group = match.groupdict()
                    method = (group.get('method') or '').upper()
                    declared = METHODS_ARGUMENT.search(group.get('rest') or '')
                    if declared:
                        method = '/'.join(sorted({value.strip().strip('\'"').upper() for value in declared.group(1).split(',') if value.strip()}))
                    if method in ('ROUTE', 'USE', 'ALL', ''):
                        method = method or 'UNSPECIFIED'
                    routes.append({'method': method, 'path_pattern': group.get('path'), 'path': unit.path, 'line': number, 'text': text})
            for pattern in ENV_PATTERNS:
                for match in pattern.finditer(text):
                    env_vars.append({'name': match.group('name'), 'path': unit.path, 'line': number, 'text': text})
            for match in RAISE_PATTERN.finditer(text):
                group = match.groupdict()
                message = group.get('message') or group.get('jsmessage') or group.get('gomessage') or group.get('panicmessage') or ''
                errors.append({'type': group.get('type') or group.get('jstype') or ('panic' if group.get('panicmessage') is not None else 'error'),
                               'message': clip(message.strip('\'"'), 120), 'path': unit.path, 'line': number, 'text': text})
            for kind, pattern in CLI_PATTERNS:
                for match in pattern.finditer(text):
                    cli.append({'kind': kind, 'name': match.group('name'), 'path': unit.path, 'line': number, 'text': text})
            if BRANCH_PATTERN.match(text):
                branches += 1
            if ENTRYPOINT.search(text):
                entrypoints.append({'path': unit.path, 'line': number, 'text': text})
            if EXTERNAL_IMPORT.match(text):
                dependencies.append({'path': unit.path, 'line': number, 'text': text.strip()})
    return {'path': unit.path, 'source': unit.source, 'language': unit.language, 'is_test': unit.is_test, 'lines': len(lines),
            'definitions': definitions, 'routes': routes, 'env_vars': env_vars, 'errors': errors,
            'branches': branches, 'entrypoints': entrypoints, 'dependencies': dependencies, 'cli': cli}


PY_TRACEBACK = re.compile(r'^\s*Traceback \(most recent call last\)')
PY_FRAME = re.compile(r'''^\s*File ["'](?P<path>[^"']+)["'], line (?P<line>\d+), in (?P<name>\S+)''')
EXC_LINE = re.compile(r'^(?P<type>(?:[A-Za-z_][\w.]*\.)?[A-Z]\w*(?:Error|Exception|Exit|Warning|Fault|Timeout|Panic))(?::\s*(?P<message>.*))?$')
JS_FRAME = re.compile(r'^\s*at\s+(?:(?P<name>[^\s(]+)\s+\()?(?P<path>[^\s():]+):(?P<line>\d+):\d+\)?')
JAVA_FRAME = re.compile(r'^\s*at\s+(?P<name>[\w.$]+)\((?P<path>[\w.$]+\.(?:java|kt)):(?P<line>\d+)\)')
GO_FRAME = re.compile(r'^\s*(?P<path>[\w./-]+\.go):(?P<line>\d+)(?:\s|$)')
GO_PANIC = re.compile(r'^\s*panic:\s*(?P<message>.*)')
HTTP_STATUS = re.compile(r'''(?:"\s*\d*\s*|\bstatus(?:_code)?[=:\s]+|\bHTTP/[\d.]+"\s+|\s)(?P<code>[45]\d\d)\b''')
LEVEL = re.compile(r'(?i)(?<![\w])(ERROR|FATAL|CRITICAL|SEVERE|PANIC|WARN(?:ING)?)(?![\w])')
LOG_PREFIX = re.compile(r'''^(?:\[[^\]]{0,120}\]|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}|(?:ERROR|WARN(?:ING)?|INFO|DEBUG|FATAL|CRITICAL|SEVERE|TRACE|NOTICE))[\s:|-]*''')
TIMESTAMP = re.compile(r'(?P<stamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})')

SIGNATURES = (
    ('connection_refused', re.compile(r'(?i)(connection refused|ECONNREFUSED|could not connect to server|connect: no route to host|failed to connect)'),
     'A dependency refused or could not accept the connection.',
     (('The dependency was not listening on the expected host or port at that moment (stopped, crashed, restarting, or a different port in this environment).',
       'Check the dependency process and listener on the target host, and compare the configured host/port for this environment with the one it binds.'),
      ('Network path or DNS resolves to the wrong address (service name, container network, firewall or security group).',
       'Resolve the hostname from inside the calling process environment and attempt a plain TCP connection to the same address and port.'))),
    ('timeout', re.compile(r'(?i)(timed? ?out|ETIMEDOUT|context deadline exceeded|read timeout|statement timeout|deadline exceeded|ESOCKETTIMEDOUT)'),
     'An operation exceeded its deadline.',
     (('The dependency is slower than the configured deadline under this load (slow query, saturated pool, cold cache or lock wait).',
       'Compare the dependency latency distribution for the incident window with the configured timeout, and check pool saturation and slow-query logs.'),
      ('The deadline is set too low for the operation, so a normal response is cut off.',
       'Measure the end-to-end duration of the same operation under normal load and compare it with the configured timeout.'))),
    ('out_of_memory', re.compile(r'(?i)(out of memory|MemoryError|OOMKilled|Killed process|exit code 137|JavaScript heap out of memory|cannot allocate memory|OutOfMemoryError)'),
     'The process hit a memory limit and was unable to allocate.',
     (('Working-set size exceeds the container or host limit, often because an unbounded result set or buffer is held in memory.',
       'Compare peak RSS or heap usage with the configured limit for the incident window, and look for calls that materialise a full result set.'),
      ('A leak accumulates across requests rather than a single large allocation.',
       'Plot memory over a longer window and check whether usage returns to baseline after load drops or only after a restart.'))),
    ('permission_denied', re.compile(r'(?i)(permission denied|EACCES|EPERM|operation not permitted|access denied)'),
     'An operation was rejected by an access control check.',
     (('The runtime user, file mode, volume mount or security context differs from the environment where it works.',
       'Compare the effective user and the target object ownership and mode in both environments.'),
      ('A credential or role lacks the required scope for the operation.',
       'Check the identity actually presented at the call site and the policy decision logged by the provider.'))),
    ('missing_file_or_module', re.compile(r'(?i)(no such file or directory|ENOENT|ModuleNotFoundError|ImportError|cannot find module|class not found|FileNotFoundError)'),
     'A required file, module or class was not found at runtime.',
     (('The artefact or dependency set differs from the environment where it works (build output, packaging, install step, working directory).',
       'List the dependency set and the resolved path inside the failing environment and compare with the working one.'),
      ('The path is relative and resolved against a different working directory.',
       'Log the absolute resolved path at the call site in the failing environment.'))),
    ('too_many_open_files', re.compile(r'(?i)(too many open files|EMFILE|socket: too many open)'),
     'The process exhausted its file-descriptor limit.',
     (('Handles or sockets are opened per request and not closed on every path.',
       'Count open descriptors for the process over time and group them by target to find the growing class.'),
      ('The descriptor limit is lower than the concurrency the service is configured for.',
       'Compare the effective ulimit with peak concurrent connections and pool sizes.'))),
    ('lock_or_deadlock', re.compile(r'(?i)(deadlock detected|deadlock found|lock wait timeout|could not obtain lock|SQLITE_BUSY|database is locked)'),
     'A lock could not be acquired or a deadlock was detected.',
     (('Two code paths take the same rows or tables in a different order inside overlapping transactions.',
       'Collect the blocking and blocked statements from the database lock view and compare their access order.'),
      ('A transaction is held open across slow work (network call, user wait) and widens the contention window.',
       'Measure transaction duration at the call site and check for non-database work inside it.'))),
    ('authentication_failure', re.compile(r'(?i)(401 unauthorized|403 forbidden|invalid (?:token|credentials|signature)|authentication failed|token (?:expired|invalid)|permission_denied: )'),
     'A request was rejected by authentication or authorisation.',
     (('The credential used in this environment is missing, stale, rotated or scoped differently.',
       'Compare the credential identity and scopes actually presented (never the value) with what the provider expects.'),
      ('Signature or expiry validation fails because of clock skew or a changed signing key.',
       'Check host clock offset and the key or issuer the token was signed with.'))),
    ('tls_failure', re.compile(r'(?i)(certificate verify failed|SSL\w*: |unable to get local issuer|x509: certificate|handshake failure|CERT_HAS_EXPIRED)'),
     'A TLS handshake or certificate validation failed.',
     (('The certificate chain presented is incomplete, expired, or issued by a CA the client does not trust in this environment.',
       'Inspect the served chain and its validity window from the calling host, and compare the trust store between environments.'),
      ('Host clock skew makes a valid certificate appear outside its window.',
       'Check the clock offset on the calling host.'))),
    ('rate_limited', re.compile(r'(?i)(429|rate limit|too many requests|quota exceeded|throttl)'),
     'A dependency or gateway rejected requests for exceeding a quota.',
     (('Request volume for this credential or route exceeded the provider quota, often amplified by retries without backoff.',
       'Compare the request rate per credential with the documented quota and check the retry policy for backoff and jitter.'),),),
    ('integrity_conflict', re.compile(r'(?i)(duplicate key|unique constraint|IntegrityError|ER_DUP_ENTRY|conflict: )'),
     'A write violated a uniqueness or integrity constraint.',
     (('The same logical operation runs twice (retry, double submit, concurrent worker) without an idempotency key.',
       'Look for a repeated request id or job id around the conflict and check whether the write path is idempotent.'),
      ('The constraint does not match the intended data model for this case.',
       'Compare the constraint definition with the intended uniqueness rule.'))),
    ('pool_exhausted', re.compile(r'(?i)(pool (?:is )?exhausted|QueuePool limit|too many clients already|connection pool timeout|no available connections)'),
     'A connection pool had no capacity for new work.',
     (('Connections are checked out longer than expected (slow queries or connections not returned on error paths).',
       'Compare checkout duration and in-use count with pool size for the incident window.'),
      ('Pool size is below the concurrency the service receives.',
       'Compare peak concurrent requests with pool size across all workers and replicas.'))),
    ('disk_full', re.compile(r'(?i)(no space left on device|ENOSPC|disk quota exceeded)'),
     'A write failed because storage was full.',
     (('A volume filled up (logs, temporary files, uploads or an unrotated artefact).',
       'Check free space and the largest directories on the affected volume and the growth rate over the window.'),),),
    ('null_dereference', re.compile(r'(?i)(NoneType[^\n]*(?:not subscriptable|has no attribute|is not iterable|not callable)|NullPointerException|cannot read propert(?:y|ies) of (?:undefined|null)|undefined is not a function|nil pointer dereference)'),
     'A value was absent where the code assumed a value.',
     (('An upstream lookup, parse or optional field returned nothing for this input and the path has no guard.',
       'Log the specific input and the value of that lookup at the frame line, then reproduce with the same input shape.'),),),
    ('type_or_key_error', re.compile(r'(?i)^(KeyError|IndexError|TypeError|ValueError|JSONDecodeError|SyntaxError|AttributeError)\b'),
     'A value had an unexpected shape, type or key.',
     (('The incoming payload or record shape differs from what the code path expects (version skew, partial write, new field, empty collection).',
       'Capture one failing input and compare its shape field by field with the shape the code path expects.'),),),
)
CHANGE_HINT = re.compile(r'(?i)(after (?:the )?(?:deploy|release|upgrade|migration)|since (?:the )?(?:deploy|release|upgrade)|just deployed|new version|rolled out|baru deploy|setelah deploy)')
ENVIRONMENT_HINT = re.compile(r'(?i)(works? (?:fine )?(?:in|on) (?:staging|dev|local)|only (?:in|on) prod|staging but not prod|prod(?:uction)? only|jalan di staging)')


def strip_log_prefix(text):
    value = text.strip()
    for _ in range(4):
        trimmed = LOG_PREFIX.sub('', value, count=1)
        if trimmed == value:
            break
        value = trimmed
    return value


def log_units(prepared):
    units = []
    if prepared.get('logs'):
        units.append(Unit('logs', LOG_IDENTIFIER, text=prepared['logs']))
    for key in ('request', 'context'):
        text = prepared.get(key) or ''
        if text and (PY_TRACEBACK.search(text) or any(pattern.search(text) for _, pattern, _, _ in SIGNATURES)
                     or any(EXC_LINE.match(line.strip()) for line in text.splitlines())):
            units.append(Unit(key, key, text=text))
    return units


def analyze_logs(units):
    """Separate what the log text states from what it merely suggests."""
    facts, frames, signatures, stamps, counters = [], [], {}, [], {'error_lines': 0, 'warning_lines': 0, 'tracebacks': 0, 'http_errors': {}}
    exceptions = {}
    for unit in units:
        for number, text, _ in unit.lines:
            stripped = text.strip()
            if not stripped:
                continue
            found = TIMESTAMP.search(text)
            if found:
                stamps.append((found.group('stamp'), unit.source, number))
            level = LEVEL.search(text)
            if level:
                if level.group(1).upper().startswith('WARN'):
                    counters['warning_lines'] += 1
                else:
                    counters['error_lines'] += 1
            core = strip_log_prefix(stripped)
            if PY_TRACEBACK.match(core):
                counters['tracebacks'] += 1
            for pattern, kind in ((PY_FRAME, 'python'), (JAVA_FRAME, 'java'), (JS_FRAME, 'javascript')):
                match = pattern.match(core)
                if match:
                    frames.append({'runtime': kind, 'frame_path': match.group('path'), 'frame_line': int(match.group('line')),
                                   'function': match.groupdict().get('name') or '', 'source': unit.source, 'path': unit.path,
                                   'line': number, 'text': stripped})
                    break
            else:
                match = GO_FRAME.match(core)
                if match and GO_PANIC.search(unit.body or ''):
                    frames.append({'runtime': 'go', 'frame_path': match.group('path'), 'frame_line': int(match.group('line')),
                                   'function': '', 'source': unit.source, 'path': unit.path, 'line': number, 'text': stripped})
            match = EXC_LINE.match(core) or GO_PANIC.match(core)
            if match:
                group = match.groupdict()
                key = (group.get('type') or 'panic', clip(group.get('message') or '', 160))
                entry = exceptions.setdefault(key, {'type': key[0], 'message': key[1], 'count': 0,
                                                    'source': unit.source, 'path': unit.path, 'line': number, 'text': stripped})
                entry['count'] += 1
            for code in HTTP_STATUS.finditer(text):
                counters['http_errors'][code.group('code')] = counters['http_errors'].get(code.group('code'), 0) + 1
            for identifier, pattern, statement, hypotheses in SIGNATURES:
                if pattern.search(text):
                    entry = signatures.setdefault(identifier, {'signature': identifier, 'statement': statement, 'count': 0,
                                                               'hypotheses': hypotheses, 'source': unit.source,
                                                               'path': unit.path, 'line': number, 'text': stripped})
                    entry['count'] += 1
    for entry in sorted(exceptions.values(), key=lambda item: (-item['count'], item['line'])):
        facts.append(entry)
    return {'exceptions': facts, 'frames': frames, 'signatures': list(signatures.values()),
            'timestamps': stamps, 'counters': counters}


SUPPRESSED_BY = {'shell_invocation': ('command_injection',), 'unbounded_result_set': ('n_plus_one_query',),
                 'credential_assignment_redacted': ('hardcoded_credential',)}
CHECKS_NOT_PERFORMED = (
    'No code was executed, compiled, type-checked or linted, so runtime and type errors can only be inferred from text.',
    'No cross-file or cross-function data-flow (taint) analysis: a sink flagged here may be reached only by trusted values, and an unflagged sink may still be reachable.',
    'No dependency, package or CVE database was consulted, so vulnerable library versions are not covered.',
    'Authentication and authorisation policy was not verified beyond the supplied lines; missing checks elsewhere are invisible.',
    'No database schema, index or query plan was inspected, so missing-index and plan regressions are not covered.',
    'No tests were run and no coverage was measured.',
    'Credential-shaped values are redacted before analysis, so this is not a secret-scanning result for the original text.',
)


def finding(ledger, unit_source, path, line, text, rule_id, dimension, severity, kind, title, recommendation, note):
    evidence_id = ledger.cite(unit_source, path, line, text)
    return {'rule': rule_id, 'dimension': dimension, 'severity': severity, 'kind': kind, 'title': title,
            'source': unit_source, 'path': path, 'line': int(line), 'evidence_id': evidence_id,
            'evidence': clip(text), 'why': note, 'recommendation': recommendation}


def order_findings(items):
    ordered = sorted(items, key=lambda item: (SEVERITY_ORDER.get(item['severity'], 9), item['path'], item['line'], item['rule']))
    capped = len(ordered) > MAX_FINDINGS
    ordered = ordered[:MAX_FINDINGS]
    for index, item in enumerate(ordered, 1):
        item['id'] = 'F%d' % index
    return ordered, capped


def review_findings(units, ledger):
    findings, positives = [], []
    for unit in units:
        by_line = {}
        for index, (number, text, reportable) in enumerate(unit.lines):
            if not reportable or not text.strip():
                continue
            comment = is_comment(text)
            for spec in LINE_RULES:
                if comment and spec['rule'] != 'todo_marker':
                    continue
                if unit.is_test and spec['rule'] in ('assert_used_for_validation', 'hardcoded_credential', 'insecure_randomness', 'debug_mode_enabled'):
                    continue
                if not spec['pattern'].search(text):
                    continue
                note = spec['guard'](text) if spec['guard'] else None
                if spec['guard'] and not note:
                    continue
                if any(other in by_line.get(number, ()) for other in SUPPRESSED_BY.get(spec['rule'], ())):
                    continue
                by_line.setdefault(number, set()).add(spec['rule'])
                findings.append(finding(ledger, unit.source, unit.path, number, text, spec['rule'], spec['dimension'],
                                        spec['severity'], spec['kind'], spec['title'], spec['recommendation'],
                                        note or 'The pattern for this check matched this line.'))
        for rule_id, number, text, note in context_hits(unit):
            dimension, severity, kind, title, recommendation = CONTEXT_RULE_META[rule_id]
            if any(other in by_line.get(number, ()) for other in SUPPRESSED_BY.get(rule_id, ())):
                continue
            by_line.setdefault(number, set()).add(rule_id)
            findings.append(finding(ledger, unit.source, unit.path, number, text, rule_id, dimension, severity, kind,
                                    title, recommendation, note))
        if unit.partial:
            for item in findings:
                if item['source'] == 'diff' and item['path'] == unit.path and 'Only the diff hunks' not in item['why']:
                    item['why'] = item['why'] + ' Only the diff hunks were supplied, so surrounding context was not available.'
    for unit, line, text, origin_path, origin_line in duplicate_blocks(units):
        findings.append(finding(ledger, unit.source, unit.path, line, text, 'duplicated_block', 'maintainability', 'low',
                                'observation', 'Duplicated block of six or more significant lines',
                                'Extract the shared block into one tested unit so a fix only has to be made once.',
                                'The same six significant lines also appear at %s line %d.' % (origin_path, origin_line)))
    for unit in units:
        for identifier, number, text, statement in positive_hits(unit):
            if len(positives) >= 8:
                break
            positives.append({'observation': statement, 'check': identifier, 'path': unit.path, 'line': number,
                              'evidence_id': ledger.cite(unit.source, unit.path, number, text)})
    return findings, positives


def code_review_report(findings, positives, units, inputs, missing):
    highs = [item for item in findings if item['severity'] == 'high']
    mediums = [item for item in findings if item['severity'] == 'medium']
    lows = [item for item in findings if item['severity'] in ('low', 'info')]
    if missing:
        verdict, basis = 'clarification_required', 'No source text was supplied, so nothing was reviewed.'
    elif highs:
        verdict, basis = 'changes_requested', '%d finding(s) of high severity matched.' % len(highs)
    elif mediums:
        verdict, basis = 'discussion_recommended', '%d finding(s) of medium severity matched.' % len(mediums)
    elif lows:
        verdict, basis = 'comments_only', 'Only low-severity findings matched.'
    else:
        verdict, basis = 'no_automated_findings', 'No configured pattern matched the supplied text.'
    scope = [{'source': unit.source, 'path': unit.path, 'language': unit.language, 'lines_analysed': len(unit.lines),
              'reportable_lines': sum(1 for _, _, reportable in unit.lines if reportable),
              'context': 'diff_hunks_only' if unit.partial else 'supplied_file_text',
              'line_limit_reached': unit.truncated} for unit in units]
    checks = {}
    for spec in LINE_RULES:
        checks.setdefault(spec['dimension'], []).append(spec['rule'])
    for rule_id, meta in CONTEXT_RULE_META.items():
        checks.setdefault(meta[0], []).append(rule_id)
    checks.setdefault('maintainability', []).append('duplicated_block')
    return {
        'summary': ('%d finding(s) from %d static check(s) over %d supplied text region(s): %d high, %d medium, %d low. '
                    'This is a heuristic pass, not an approval and not a security clearance.'
                    % (len(findings), sum(len(values) for values in checks.values()), len(units), len(highs), len(mediums), len(lows))),
        'verdict': verdict, 'verdict_basis': basis,
        'verdict_meaning': 'A verdict reflects only the configured checks above; human review of logic, intent and threat model is still required.',
        'critical_issues': [{'id': item['id'], 'path': item['path'], 'line': item['line'], 'issue': item['title'],
                             'severity': item['severity'], 'dimension': item['dimension'], 'kind': item['kind'],
                             'evidence_id': item['evidence_id']} for item in (highs + mediums)[:MAX_REPORT_ITEMS]],
        'suggestions': [{'id': item['id'], 'path': item['path'], 'line': item['line'], 'suggestion': item['recommendation'],
                         'category': item['dimension'], 'evidence_id': item['evidence_id']} for item in lows[:MAX_REPORT_ITEMS]],
        'what_looks_good': positives,
        'what_looks_good_meaning': 'Each item is one matched positive pattern at the cited line, not a judgement about the file as a whole.',
        'hypotheses': [{'id': item['id'], 'statement': item['title'], 'path': item['path'], 'line': item['line'],
                        'needs_verification': item['why'], 'evidence_id': item['evidence_id']}
                       for item in findings if item['kind'] == 'hypothesis'][:MAX_REPORT_ITEMS],
        'review_scope': scope,
        'dimensions_checked': [{'dimension': name, 'checks': sorted(set(values))} for name, values in sorted(checks.items())],
        'checks_not_performed': list(CHECKS_NOT_PERFORMED),
        'missing_context': missing,
        'inputs': inputs,
    }


REPRO_LABELS = (
    ('expected', re.compile(r'''(?i)^[\s\-*#>]*(?:expected(?:\s+behaviou?r|\s+result)?|yang diharapkan|seharusnya)\s*[:\-]\s*(?P<value>.*)$''')),
    ('actual', re.compile(r'''(?i)^[\s\-*#>]*(?:actual(?:\s+behaviou?r|\s+result)?|observed|yang terjadi|aktual)\s*[:\-]\s*(?P<value>.*)$''')),
    ('steps', re.compile(r'''(?i)^[\s\-*#>]*(?:steps?(?:\s+to\s+reproduce)?|reproduction|repro|langkah)\s*[:\-]\s*(?P<value>.*)$''')),
    ('environment', re.compile(r'''(?i)^[\s\-*#>]*(?:environment|env|platform|runtime|version)\s*[:\-]\s*(?P<value>.*)$''')),
    ('scope', re.compile(r'''(?i)^[\s\-*#>]*(?:impact|affected|scope|since|frequency)\s*[:\-]\s*(?P<value>.*)$''')),
)
DEBUG_QUESTIONS = (
    'Paste the error output verbatim, including the full stack trace and the surrounding log lines.',
    'What is the expected behaviour and what happens instead, for one concrete input?',
    'What are the exact steps that reproduce it, and does it reproduce every time?',
    'When did it start, and what changed around then (deploy, dependency upgrade, configuration or data change)?',
    'Which environment, version and scope is affected (all requests, one tenant, one endpoint)?',
)


def reproduction_block(prepared, ledger):
    block = {'expected': '', 'actual': '', 'steps': '', 'environment': '', 'scope': '', 'evidence_ids': []}
    for key in ('request', 'context'):
        text = prepared.get(key) or ''
        for number, line in enumerate(str(text).splitlines(), 1):
            for label, pattern in REPRO_LABELS:
                match = pattern.match(line)
                if match and not block[label]:
                    value = match.group('value').strip()
                    if value:
                        block[label] = clip(value)
                        block['evidence_ids'].append(ledger.cite(key, key, number, line))
    supplied = [label for label in ('expected', 'actual', 'steps') if block[label]]
    block['status'] = 'supplied' if len(supplied) == 3 else ('partial' if supplied else 'not_supplied')
    block['supplied_fields'] = supplied
    block['note'] = 'Fields are quoted from the request text; nothing here was reproduced or verified by this analysis.'
    return block


def join_frames(frames, file_units, ledger):
    """Map stack frames onto supplied files without inventing a mapping."""
    index = {}
    for unit in file_units:
        index[unit.path] = unit
    joined = []
    for frame in frames[:MAX_REPORT_ITEMS]:
        frame_path = str(frame.get('frame_path') or '')
        base = frame_path.rsplit('/', 1)[-1]
        matched = None
        for path, unit in index.items():
            if path == frame_path or path.endswith('/' + base) or path == base or frame_path.endswith('/' + path):
                matched = unit
                break
        item = {'runtime': frame['runtime'], 'frame': '%s:%s' % (frame_path, frame['frame_line']),
                'function': frame['function'], 'log_evidence_id': ledger.cite(frame['source'], frame['path'], frame['line'], frame['text']),
                'supplied_file': matched.path if matched else '', 'source_evidence_id': '', 'source_line': '',
                'static_check_at_frame': []}
        if matched:
            for number, text, _ in matched.lines:
                if number == frame['frame_line']:
                    item['source_line'] = clip(text)
                    item['source_evidence_id'] = ledger.cite(matched.source, matched.path, number, text)
                    for spec in LINE_RULES:
                        if spec['pattern'].search(text) and (not spec['guard'] or spec['guard'](text)):
                            item['static_check_at_frame'].append(spec['rule'])
                    break
            if not item['source_evidence_id']:
                item['source_line'] = 'line %d is outside the supplied text for this file' % frame['frame_line']
        joined.append(item)
    return joined


def debug_report(prepared, logs, file_units, ledger, inputs, missing):
    stamps = logs['timestamps']
    counters = logs['counters']
    facts, hypotheses = [], []
    for entry in logs['exceptions'][:MAX_REPORT_ITEMS]:
        facts.append({'fact': 'The supplied text reports %s%s, %d time(s).' % (entry['type'], ': ' + entry['message'] if entry['message'] else '', entry['count']),
                      'evidence_id': ledger.cite(entry['source'], entry['path'], entry['line'], entry['text']),
                      'kind': 'observation'})
    for entry in logs['signatures']:
        facts.append({'fact': '%s Matched %d line(s).' % (entry['statement'].rstrip('.') + '.', entry['count']),
                      'evidence_id': ledger.cite(entry['source'], entry['path'], entry['line'], entry['text']),
                      'kind': 'observation'})
        basis = [facts[-1]['evidence_id']]
        for statement, test in entry['hypotheses']:
            hypotheses.append({'statement': statement, 'kind': 'hypothesis', 'basis': basis,
                               'discriminating_test': test, 'signature': entry['signature']})
    frames = join_frames(logs['frames'], file_units, ledger)
    for frame in frames:
        if frame['supplied_file'] and frame['source_evidence_id']:
            facts.append({'fact': 'Frame %s maps to supplied file %s, whose line reads: %s' % (frame['frame'], frame['supplied_file'], frame['source_line']),
                          'evidence_id': frame['source_evidence_id'], 'kind': 'observation'})
    request_text = (prepared.get('request') or '') + '\n' + (prepared.get('context') or '')
    if CHANGE_HINT.search(request_text):
        hypotheses.append({'statement': 'A recent deploy, dependency upgrade or configuration change introduced the failure, because the request states the failure began around such a change.',
                           'kind': 'hypothesis', 'basis': [], 'discriminating_test': 'Compare the running revision and effective configuration before and after the stated change, then check whether the failure rate starts at that boundary.',
                           'signature': 'change_correlation'})
    if ENVIRONMENT_HINT.search(request_text):
        hypotheses.append({'statement': 'The failure depends on an environment difference (configuration, credentials, data volume, resource limits or dependency version) rather than on the code path alone.',
                           'kind': 'hypothesis', 'basis': [], 'discriminating_test': 'Diff the effective configuration and dependency versions between the two environments, then reproduce with production-shaped data in the working environment.',
                           'signature': 'environment_difference'})
    generic_only = bool(counters['error_lines'] or counters['warning_lines']) and not logs['exceptions'] and not logs['signatures'] and not logs['frames']
    root_cause = {'status': 'not_established', 'statement': '', 'why': '', 'kind': 'conclusion_withheld'}
    located = [(index, frame) for index, frame in enumerate(frames) if frame['source_evidence_id']]
    if missing:
        root_cause['why'] = 'No diagnostic text was supplied, so no fact about the failure is available.'
    elif generic_only:
        root_cause['why'] = ('The supplied text contains only generic failure wording with no exception type, signature or stack frame. '
                             'A root cause cannot be derived from a generic error message.')
    elif located:
        index, frame = located[-1]
        log_line = logs['frames'][index]['line'] if index < len(logs['frames']) else 0
        after = sorted((entry for entry in logs['exceptions'] if entry['line'] > log_line), key=lambda entry: entry['line'])
        reported = after[0] if after else (logs['exceptions'][0] if logs['exceptions'] else None)
        root_cause = {
            'status': 'location_identified', 'kind': 'observation',
            'failing_location': frame['frame'], 'supplied_file': frame['supplied_file'], 'source_line': frame['source_line'],
            'reported_error': ('%s%s' % (reported['type'], ': ' + reported['message'] if reported['message'] else '')) if reported else '',
            'static_checks_at_location': frame['static_check_at_frame'],
            'statement': 'The deepest mapped stack frame is %s, which is a supplied line reading: %s. This locates where the failure surfaced.'
                         % (frame['frame'], frame['source_line']),
            'why': ('Mapping a frame to a supplied line is an observation, not a cause. Why that line failed depends on the input and state at '
                    'that moment, which static text analysis cannot determine: reproduce with the failing input and observe the values there. '
                    'Any static check listed for this location was matched independently and may be unrelated to this failure.'),
        }
    elif logs['exceptions'] or logs['signatures']:
        root_cause['why'] = ('Error signatures were identified, but no stack frame maps to a supplied source line, so the responsible code path was not located. '
                             'Supply the files named in the stack trace to narrow it.')
    else:
        root_cause['why'] = 'The supplied text contains no exception, signature or frame that this analysis can attribute.'
    diagnostics = ['Reproduce with one concrete failing input and capture the full trace, the input shape and the effective configuration at that moment.']
    for item in hypotheses[:6]:
        diagnostics.append(item['discriminating_test'])
    if logs['frames'] and not any(frame['supplied_file'] for frame in frames):
        diagnostics.append('Supply the source of the files named in the stack frames so the failing lines can be examined.')
    if counters['http_errors']:
        diagnostics.append('Split the %s responses by route, caller and tenant to see whether the failure is global or scoped.'
                           % '/'.join(sorted(counters['http_errors'])))
    prevention = ['Add a regression test that reproduces the failing input and asserts the handled behaviour, and keep it in the normal suite.']
    if logs['signatures']:
        prevention.append('Add an alert or assertion for the specific signature observed (%s) so the next occurrence is detected before a report.'
                          % ', '.join(sorted(entry['signature'] for entry in logs['signatures'])))
    if any(frame['static_check_at_frame'] for frame in frames):
        prevention.append('Guard the frame line identified above with an explicit check and a typed error instead of relying on the caller.')
    return {
        'reproduction': reproduction_block(prepared, ledger),
        'observed_facts': facts[:MAX_REPORT_ITEMS],
        'facts_meaning': 'Each fact restates what the supplied text says at the cited evidence line. It does not assert a cause.',
        'error_signatures': [{'signature': entry['signature'], 'statement': entry['statement'], 'occurrences': entry['count']}
                             for entry in logs['signatures']],
        'stack_frames': frames,
        'timeline': {'first_timestamp': stamps[0][0] if stamps else '', 'last_timestamp': stamps[-1][0] if stamps else '',
                     'timestamped_lines': len(stamps), 'error_level_lines': counters['error_lines'],
                     'warning_level_lines': counters['warning_lines'], 'traceback_blocks': counters['tracebacks'],
                     'http_error_counts': counters['http_errors'],
                     'note': 'Timestamps are read in the order supplied and are not normalised to a timezone.'},
        'hypotheses': hypotheses[:MAX_REPORT_ITEMS],
        'hypotheses_meaning': 'Each hypothesis is a candidate explanation with the test that would confirm or refute it. None has been tested here.',
        'root_cause': root_cause,
        'next_diagnostics': diagnostics[:MAX_REPORT_ITEMS],
        'prevention': prevention,
        'proposed_fix': {'status': 'withheld', 'why': 'A fix is proposed only after a cause is confirmed against the failing input; this analysis cannot execute or reproduce anything.'},
        'missing_context': missing,
        'inputs': inputs,
    }


def debug_findings(logs, report, ledger):
    findings = []
    for entry in logs['exceptions'][:MAX_REPORT_ITEMS]:
        findings.append(finding(ledger, entry['source'], entry['path'], entry['line'], entry['text'],
                                'reported_exception', 'diagnostics', 'high' if entry['count'] > 1 else 'medium',
                                'observation', 'Reported %s' % entry['type'],
                                'Locate the frame that raises it and capture the input that reaches that line.',
                                'This line states the failure; the count across the supplied text is %d.' % entry['count']))
    for entry in logs['signatures']:
        findings.append(finding(ledger, entry['source'], entry['path'], entry['line'], entry['text'],
                                entry['signature'], 'diagnostics', 'medium', 'observation', entry['statement'],
                                'Run the discriminating test listed for this signature in report.hypotheses before changing code.',
                                'The %s signature matched %d line(s).' % (entry['signature'], entry['count'])))
    for frame in report['stack_frames']:
        if frame['static_check_at_frame'] and frame['source_evidence_id']:
            findings.append({'rule': 'static_check_at_failing_frame', 'dimension': 'diagnostics', 'severity': 'high',
                             'kind': 'hypothesis', 'title': 'Static check matches the line named by a stack frame',
                             'source': 'file', 'path': frame['supplied_file'],
                             'line': int(frame['frame'].rsplit(':', 1)[-1] or 0), 'evidence_id': frame['source_evidence_id'],
                             'evidence': frame['source_line'],
                             'why': 'Frame %s maps to this supplied line and the check(s) %s match it independently. Correlation only: the check may be unrelated to the reported error, so confirm by reproducing.'
                                    % (frame['frame'], ', '.join(frame['static_check_at_frame'])),
                             'recommendation': 'Reproduce with the failing input and inspect the values at this line before editing.'})
    return findings


PARAM_CASES = (
    (re.compile(r'(?i)(list|items?|rows?|ids|array|collection|batch|records?|entries)$'),
     (('empty collection', 'An empty input is the most common unhandled shape for a collection parameter.'),
      ('single element and duplicate elements', 'Boundary between "one" and "many", and duplicate handling, are separate behaviours.'))),
    (re.compile(r'(?i)^(n|count|limit|size|offset|page|quantity|qty|amount|total|max|min|length)'),
     (('zero and negative values', 'Numeric parameters are commonly assumed positive without a guard.'),
      ('value above the expected maximum', 'Upper-bound behaviour (clamp, reject or overflow) must be chosen deliberately.'))),
    (re.compile(r'(?i)(path|file|filename|dir|directory)'),
     (('missing path and a path that escapes the allowed root', 'Path handling decides both an error path and a security boundary.'),),),
    (re.compile(r'(?i)(url|uri|endpoint|host|address)'),
     (('malformed target and unreachable target', 'Parsing failure and connection failure are different error paths.'),),),
    (re.compile(r'(?i)(token|secret|password|credential|auth|signature|key)'),
     (('absent credential and invalid or expired credential', 'Each must fail closed with a distinct, non-leaking error.'),),),
    (re.compile(r'(?i)(user|actor|account|tenant|owner|role|principal)'),
     (('actor without permission for the target object', 'Authorisation is a behaviour that needs its own assertion, not only authentication.'),),),
    (re.compile(r'(?i)(date|time|timestamp|timezone|tz|expiry|expires)'),
     (('timezone and daylight-saving boundary values', 'Date arithmetic fails at offsets rather than in the middle of a range.'),),),
    (re.compile(r'(?i)(payload|body|data|json|request|event|message)'),
     (('missing required field, wrong field type, and an unexpected extra field', 'Payload shape skew is the usual cause of production decode failures.'),),),
    (re.compile(r'(?i)(text|name|title|description|query|search|input|str|comment)'),
     (('empty string, very long value, and non-ASCII characters', 'String bounds and encoding are cheap to test and commonly unhandled.'),),),
    (re.compile(r'(?i)(^id$|_id$|uuid|slug|code|ref)'),
     (('unknown identifier', 'The not-found path needs a defined response rather than an exception leak.'),),),
    (re.compile(r'(?i)^(is_|has_|should_|enable|disable|flag|force|dry_run)'),
     (('both boolean branches', 'A flag doubles the behaviour of the unit; each branch needs one assertion.'),),),
)


def param_cases(name):
    for pattern, cases in PARAM_CASES:
        if pattern.search(name):
            return list(cases)
    return [('absent or null value for %s' % name, 'The unit should either require the value explicitly or define its default behaviour.')]


def testing_report(structures, prepared, ledger, inputs):
    sources = [item for item in structures if not item['is_test']]
    tests = [item for item in structures if item['is_test']]
    test_text = ' '.join(definition['name'] for item in tests for definition in item['definitions'])
    test_names = [{'path': item['path'], 'test_names': [definition['name'] for definition in item['definitions']][:MAX_REPORT_ITEMS],
                   'definitions': len(item['definitions'])} for item in tests]
    cases, gaps = [], []
    for item in sources:
        for definition in item['definitions'][:8]:
            if definition['kind'] != 'function':
                continue
            evidence_id = ledger.cite(item['source'], definition['path'], definition['line'], definition['text'])
            cases.append({'layer': 'unit', 'target': definition['name'], 'path': definition['path'], 'line': definition['line'],
                          'case': 'documented behaviour for a representative valid input',
                          'rationale': 'The happy path anchors the other cases and fails loudly when the signature changes.',
                          'evidence_id': evidence_id})
            for parameter in definition['parameters'][:3]:
                for case, rationale in param_cases(parameter)[:2]:
                    cases.append({'layer': 'unit', 'target': '%s(%s)' % (definition['name'], parameter), 'path': definition['path'],
                                  'line': definition['line'], 'case': case, 'rationale': rationale, 'evidence_id': evidence_id})
        for error in item['errors'][:8]:
            cases.append({'layer': 'unit', 'target': '%s:%d' % (error['path'], error['line']),
                          'path': error['path'], 'line': error['line'],
                          'case': 'input that triggers %s%s' % (error['type'], (' (' + error['message'] + ')') if error['message'] else ''),
                          'rationale': 'The error path exists in the supplied source, so its trigger and message are testable behaviour.',
                          'evidence_id': ledger.cite(item['source'], error['path'], error['line'], error['text'])})
        for route in item['routes'][:8]:
            evidence_id = ledger.cite(item['source'], route['path'], route['line'], route['text'])
            cases.append({'layer': 'integration', 'target': '%s %s' % (route['method'], route['path_pattern']),
                          'path': route['path'], 'line': route['line'],
                          'case': 'valid request returns the documented status and body shape; malformed body is rejected with a client error',
                          'rationale': 'The route is declared in the supplied source; HTTP status and decoding belong to the integration layer.',
                          'evidence_id': evidence_id})
            cases.append({'layer': 'integration', 'target': '%s %s' % (route['method'], route['path_pattern']),
                          'path': route['path'], 'line': route['line'],
                          'case': 'anonymous and under-privileged callers, if this route is meant to be protected',
                          'rationale': 'Whether this route requires authorisation is not visible in the supplied text; confirm the intent, then assert it.',
                          'evidence_id': evidence_id})
        for dependency in item['dependencies'][:5]:
            cases.append({'layer': 'integration', 'target': dependency['text'], 'path': dependency['path'], 'line': dependency['line'],
                          'case': 'dependency failure, timeout and malformed response at this boundary',
                          'rationale': 'An external boundary is imported here, so failure handling at that boundary is testable with a stub or fake.',
                          'evidence_id': ledger.cite(item['source'], dependency['path'], dependency['line'], dependency['text'])})
        for entry in item['entrypoints'][:2]:
            cases.append({'layer': 'end_to_end', 'target': entry['path'], 'path': entry['path'], 'line': entry['line'],
                          'case': 'one critical path through the entrypoint with real wiring and a known fixture',
                          'rationale': 'An entrypoint exists in the supplied source, so a single smoke path is worth its cost.',
                          'evidence_id': ledger.cite(item['source'], entry['path'], entry['line'], entry['text'])})
        for definition in item['definitions']:
            if definition['kind'] == 'function' and definition['exported'] and definition['name'] not in test_text:
                gaps.append(finding(ledger, item['source'], definition['path'], definition['line'], definition['text'],
                                    'no_supplied_test_references_unit', 'test_debt', 'medium', 'hypothesis',
                                    'No supplied test name references %s' % definition['name'],
                                    'Add a unit test for this function, or point the analysis at the test file that already covers it.',
                                    'Name-based comparison against the supplied test files only. This is not a coverage measurement and a test may cover it under a different name.'))
    requirements = [clip(line) for line in str(prepared.get('request') or '').splitlines() if line.strip()][:8]
    layers = {
        'unit': {'focus': 'Pure logic, branch and boundary behaviour of the units listed below, with dependencies replaced by fakes.',
                 'proposed_case_count': sum(1 for case in cases if case['layer'] == 'unit')},
        'integration': {'focus': 'Declared routes and external boundaries: serialisation, status codes, error mapping, timeouts and retries.',
                        'proposed_case_count': sum(1 for case in cases if case['layer'] == 'integration')},
        'end_to_end': {'focus': 'A small number of critical paths through the real wiring; keep them few because they are slow and brittle.',
                       'proposed_case_count': sum(1 for case in cases if case['layer'] == 'end_to_end')},
    }
    return {
        'scope': {'source_files': [item['path'] for item in sources], 'test_files_supplied': [item['path'] for item in tests],
                  'units_detected': sum(len(item['definitions']) for item in sources),
                  'routes_detected': sum(len(item['routes']) for item in sources),
                  'error_paths_detected': sum(len(item['errors']) for item in sources),
                  'branch_lines_detected': sum(item['branches'] for item in sources)},
        'requirements_quoted_from_request': requirements,
        'layers': layers,
        'proposed_cases': cases[:MAX_REPORT_ITEMS],
        'proposed_cases_meaning': 'Cases are derived from signatures, declared routes, raise sites and imports found in the supplied text. They are a plan to write, not tests that exist or have run.',
        'existing_tests': test_names,
        'not_worth_testing': ['Trivial accessors and pass-through wrappers with no branch.', 'Third-party framework behaviour already covered upstream.',
                              'One-off scripts that will not be maintained; prefer a smoke check instead.'],
        'coverage': {'measured': False, 'target': 'not asserted here',
                     'statement': 'No tests were executed and no coverage was measured. Any gap listed is a name-based heuristic over the supplied files only.'},
        'how_to_verify': ['Write the cases above as failing tests first, then run the suite with the project runner.',
                          'Measure coverage with the project tooling and compare it with the units listed in scope.',
                          'Re-run this analysis with the test files supplied to narrow the name-based gaps.'],
        'inputs': inputs,
    }, gaps


DOC_TYPES = (
    ('runbook', re.compile(r'(?i)(runbook|run book|on-?call|playbook|operational procedure|prosedur operasional)')),
    ('api_reference', re.compile(r'(?i)(api (?:doc|reference|documentation)|endpoint doc|openapi|swagger|dokumentasi api)')),
    ('architecture_doc', re.compile(r'(?i)(architecture doc|arsitektur|design doc|system overview|adr\b)')),
    ('onboarding_guide', re.compile(r'(?i)(onboarding|getting started for new|new (?:hire|engineer|developer)|panduan awal)')),
    ('readme', re.compile(r'(?i)(readme|quick ?start|getting started|install(?:ation)? guide)')),
)
OUTLINES = {
    'readme': (('What this is and why it exists', 'purpose', 'One short paragraph a newcomer can act on.'),
               ('Install', 'install', 'Exact commands and the dependency set actually required.'),
               ('Quick start', 'quick_start', 'Shortest path from clone to first successful run.'),
               ('Configuration', 'configuration', 'Every variable the code reads, with required/optional and effect.'),
               ('Usage', 'surface', 'The entry points callers actually use.'),
               ('Limits and known gaps', 'limits', 'What it does not do, stated plainly.'),
               ('Contributing and tests', 'contributing', 'How to run the suite and what review expects.')),
    'api_reference': (('Overview and base URL', 'purpose', 'What the API is for and where it is served.'),
                      ('Authentication', 'auth', 'Scheme, where the credential goes, and failure responses.'),
                      ('Endpoints', 'api', 'One entry per declared route with request and response shape.'),
                      ('Errors', 'errors', 'Error types the code raises and what the caller should do.'),
                      ('Rate limits and pagination', 'limits', 'Only what the code or config actually enforces.'),
                      ('Configuration', 'configuration', 'Variables that change API behaviour.')),
    'runbook': (('When to use this runbook', 'purpose', 'The symptom or alert that brings someone here.'),
                ('Prerequisites and access', 'access', 'Accounts, roles and tools needed before starting.'),
                ('Diagnostic steps', 'diagnostics', 'Ordered checks with the command and the expected output.'),
                ('Mitigation', 'operations', 'The action for each diagnosis, with its blast radius.'),
                ('Rollback', 'rollback', 'How to undo each action and how to verify the undo.'),
                ('Escalation', 'escalation', 'Who to page, when, and with what context.')),
    'architecture_doc': (('Context and goals', 'purpose', 'The problem, the constraints and what is out of scope.'),
                         ('Components', 'architecture', 'The units in the supplied source and their responsibility.'),
                         ('Data flow and integrations', 'data_flow', 'External boundaries the code actually calls.'),
                         ('Key decisions and trade-offs', 'decisions', 'Each decision with what it makes harder.'),
                         ('Configuration and environments', 'configuration', 'What differs per environment.'),
                         ('Open questions', 'open_questions', 'What is undecided, so readers do not guess.')),
    'onboarding_guide': (('What you will be able to do', 'purpose', 'The first task a new engineer can complete.'),
                         ('Environment setup', 'install', 'Commands, versions and the dependency set.'),
                         ('How the pieces fit', 'architecture', 'The modules in the supplied source and their roles.'),
                         ('Common tasks', 'quick_start', 'Walkthroughs for the entry points that exist.'),
                         ('Tests and review', 'contributing', 'How to verify work before asking for review.'),
                         ('Who to ask', 'escalation', 'Ownership per area; needs an owner to fill in.')),
}
REQUIRES_INPUT = {
    'auth': 'The authentication scheme is not determinable from the supplied text. Ask the owner which scheme applies and document the real failure responses.',
    'access': 'Required accounts, roles and tools are not in the supplied text. Ask the operator who owns them.',
    'diagnostics': 'Diagnostic commands and their expected output must come from someone who has run them; do not invent command output.',
    'operations': 'Mitigation steps and their blast radius must be confirmed by the service owner before they are written down.',
    'rollback': 'Rollback steps and their verification must be supplied by the owner; an unverified rollback is worse than none.',
    'escalation': 'Ownership and paging routes are organisational facts that are not present in the supplied text.',
    'decisions': 'Design decisions and their trade-offs are not recoverable from source text alone; ask the deciders.',
    'limits': 'Documented limits must match enforced limits. Only state a limit that exists in configuration or code.',
    'open_questions': 'Collect the questions the supplied source cannot answer, and keep them visible instead of guessing.',
}


def documentation_report(structures, prepared, ledger, inputs):
    request = (prepared.get('request') or '') + '\n' + (prepared.get('context') or '')
    document_type, basis = 'readme', 'Defaulted to a README because the request did not name a document type.'
    for name, pattern in DOC_TYPES:
        if pattern.search(request):
            document_type, basis = name, 'Selected from wording in the request.'
            break
    routes, env_vars, definitions, errors, entrypoints, dependencies, commands = [], [], [], [], [], [], []
    for item in structures:
        for route in item['routes']:
            routes.append({'method': route['method'], 'path_pattern': route['path_pattern'], 'declared_in': route['path'],
                           'line': route['line'], 'evidence_id': ledger.cite(item['source'], route['path'], route['line'], route['text'])})
        for variable in item['env_vars']:
            if variable['name'] not in [entry['name'] for entry in env_vars]:
                env_vars.append({'name': variable['name'], 'declared_in': variable['path'], 'line': variable['line'],
                                 'required_or_default': 'not determinable from the supplied line',
                                 'source': item['source'], 'text': variable['text'],
                                 'evidence_id': ledger.cite(item['source'], variable['path'], variable['line'], variable['text'])})
        for definition in item['definitions']:
            if definition['exported'] and not item['is_test']:
                definitions.append({'kind': definition['kind'], 'name': definition['name'], 'parameters': definition['parameters'],
                                    'declared_in': definition['path'], 'line': definition['line'], 'documented_in_source': definition['documented'],
                                    'source': item['source'], 'text': definition['text'],
                                    'evidence_id': ledger.cite(item['source'], definition['path'], definition['line'], definition['text'])})
        for error in item['errors']:
            errors.append({'type': error['type'], 'message': error['message'], 'declared_in': error['path'], 'line': error['line'],
                           'evidence_id': ledger.cite(item['source'], error['path'], error['line'], error['text'])})
        for entry in item['entrypoints']:
            entrypoints.append({'declared_in': entry['path'], 'line': entry['line'],
                                'evidence_id': ledger.cite(item['source'], entry['path'], entry['line'], entry['text'])})
        for command in item['cli']:
            commands.append({'kind': command['kind'], 'name': command['name'], 'declared_in': command['path'], 'line': command['line'],
                             'evidence_id': ledger.cite(item['source'], command['path'], command['line'], command['text'])})
        dependencies += [{'import': entry['text'], 'declared_in': entry['path'], 'line': entry['line']} for entry in item['dependencies']]
    content = {
        'purpose': [clip(line) for line in str(prepared.get('request') or '').splitlines() if line.strip()][:4],
        'install': ['Dependency imported at %s:%d: %s' % (entry['declared_in'], entry['line'], entry['import']) for entry in dependencies[:8]],
        'quick_start': (['Entrypoint at %s:%d' % (entry['declared_in'], entry['line']) for entry in entrypoints[:4]]
                        + ['%s "%s" declared at %s:%d' % (entry['kind'].capitalize(), entry['name'], entry['declared_in'], entry['line'])
                           for entry in commands[:8]]),
        'configuration': ['%s read at %s:%d' % (entry['name'], entry['declared_in'], entry['line']) for entry in env_vars[:MAX_REPORT_ITEMS]],
        'surface': ['%s %s(%s) at %s:%d' % (entry['kind'], entry['name'], ', '.join(entry['parameters']), entry['declared_in'], entry['line'])
                    for entry in definitions[:MAX_REPORT_ITEMS]],
        'api': ['%s %s declared at %s:%d' % (entry['method'], entry['path_pattern'], entry['declared_in'], entry['line']) for entry in routes[:MAX_REPORT_ITEMS]],
        'errors': ['%s%s raised at %s:%d' % (entry['type'], (' (' + entry['message'] + ')') if entry['message'] else '', entry['declared_in'], entry['line'])
                   for entry in errors[:MAX_REPORT_ITEMS]],
        'architecture': ['%s (%s, %d lines, %d definitions)' % (item['path'], item['language'] or 'language not detected', item['lines'], len(item['definitions']))
                         for item in structures[:MAX_REPORT_ITEMS]],
        'data_flow': ['External boundary at %s:%d: %s' % (entry['declared_in'], entry['line'], entry['import']) for entry in dependencies[:8]],
        'contributing': ['Supplied test file: ' + item['path'] for item in structures if item['is_test']],
    }
    outline = []
    for section, key, purpose in OUTLINES[document_type]:
        grounded = content.get(key) or []
        if grounded:
            outline.append({'section': section, 'purpose': purpose, 'status': 'draft_from_supplied_source', 'content_from_source': grounded})
        else:
            outline.append({'section': section, 'purpose': purpose, 'status': 'requires_input',
                            'needs': REQUIRES_INPUT.get(key, 'Nothing in the supplied text grounds this section; ask the owner rather than inventing it.')})
    gaps = []
    for entry in definitions[:MAX_REPORT_ITEMS]:
        if not entry['documented_in_source']:
            gaps.append({'path': entry['declared_in'], 'line': entry['line'], 'evidence_id': entry['evidence_id'],
                         'source': entry['source'], 'text': entry['text'], 'item': '%s %s' % (entry['kind'], entry['name']),
                         'gap': 'No docstring or leading comment in the supplied text.'})
    for entry in env_vars[:MAX_REPORT_ITEMS]:
        gaps.append({'path': entry['declared_in'], 'line': entry['line'], 'evidence_id': entry['evidence_id'],
                     'source': entry['source'], 'text': entry['text'], 'item': entry['name'],
                     'gap': 'Configuration variable read in code; its required/optional status, default and effect are not stated in the supplied text.'})
    return {
        'document_type': document_type, 'document_type_basis': basis,
        'audience': 'Stated audience not supplied; drafted for an engineer new to this code. Confirm the audience before publishing.',
        'outline': outline,
        'api_surface': definitions[:MAX_REPORT_ITEMS],
        'http_routes': routes[:MAX_REPORT_ITEMS] or [],
        'http_routes_note': ('%d route declaration(s) found in the supplied source.' % len(routes)) if routes
                            else 'No route declaration was found in the supplied source. Do not document endpoints that are not in the supplied routing code.',
        'configuration': env_vars[:MAX_REPORT_ITEMS],
        'commands': commands[:MAX_REPORT_ITEMS],
        'entrypoints': entrypoints[:MAX_REPORT_ITEMS],
        'error_reference': errors[:MAX_REPORT_ITEMS],
        'documentation_gaps': gaps[:MAX_REPORT_ITEMS],
        'never_invent': ['Endpoints, parameters, status codes or response bodies that are not in the supplied routing code.',
                         'Command output, benchmark numbers, screenshots or installation results, none of which were produced here.',
                         'Version numbers, support guarantees, SLAs, ownership or escalation routes.',
                         'Behaviour of code that was not supplied, including defaults implemented elsewhere.'],
        'inputs': inputs,
    }, gaps


OPERATION_LIMITATIONS = {
    'code_review': (
        'Findings are pattern matches at single lines or within one supplied region, so a value may be trusted or validated elsewhere (false positive) and an unmatched sink may still be exploitable (false negative).',
        'Severity reflects the configured check, not this system\'s threat model, exposure or data sensitivity; re-rank with that context.',
        'The verdict is a heuristic summary and never an approval, a security sign-off or a substitute for human review.',
    ),
    'debug': (
        'Facts restate the supplied text; hypotheses are untested candidates. Nothing was reproduced, executed, measured or confirmed here.',
        'A root cause is reported as a candidate only when a stack frame maps to a supplied line and a static check matches it, and still requires reproduction to confirm.',
        'Log analysis sees only the supplied excerpt: earlier causes, dropped lines, other services and metrics outside this text are invisible, and timestamps are not timezone-normalised.',
    ),
    'testing_strategy': (
        'Proposed cases are a plan derived from signatures, routes, raise sites and imports in the supplied text. No test was written, executed or validated.',
        'Coverage was not measured. A gap means no supplied test name referenced the unit, which is a name-based heuristic, not proof that it is untested.',
        'Case lists are bounded, so large files yield a sample rather than an exhaustive plan.',
    ),
    'documentation': (
        'Draft content is limited to what the supplied source states. Endpoints, parameters, defaults, command output and operational steps are never inferred.',
        'Sections marked requires_input have no grounding in the supplied text and must be filled by an owner rather than guessed.',
        'Extraction is pattern-based, so a route, variable or definition expressed in an unusual form may be missed.',
    ),
}
CODE_REVIEW_QUESTIONS = (
    'Supply the changed files or the unified diff you want reviewed; no source text was present in the payload.',
    'Say which dimension matters most here (security, performance, correctness, maintainability) and whether this path is hot or handles sensitive data.',
)


def inputs_block(prepared, source_units, logs_units, warnings):
    diff_units = [unit for unit in source_units if unit.source == 'diff']
    file_units_only = [unit for unit in source_units if unit.source == 'file']
    return {
        'files_supplied': len(file_units_only),
        'file_lines_analysed': sum(len(unit.lines) for unit in file_units_only),
        'diff_files_parsed': len(diff_units),
        'diff_added_lines_analysed': sum(sum(1 for _, _, reportable in unit.lines if reportable) for unit in diff_units),
        'log_lines_analysed': sum(len(unit.lines) for unit in logs_units if unit.source == 'logs'),
        'request_characters': len(str(prepared.get('request') or '')),
        'context_characters': len(str(prepared.get('context') or '')),
        'redaction_count': int(prepared.get('redaction_count') or 0),
        'upstream_truncated': bool(prepared.get('truncated')),
        'upstream_warnings': [clip(str(item)) for item in (prepared.get('warnings') if isinstance(prepared.get('warnings'), list) else [])][:10],
        'response_bound_bytes': MAX_JSON_BYTES,
        'analyzer_warnings': warnings[:10],
        'line_limit_reached': [unit.path for unit in source_units + logs_units if unit.truncated],
    }


def measured(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def shorten_report_lists(result):
    report = result['report']
    for key in list(report):
        if isinstance(report.get(key), list) and len(report[key]) > 10:
            report[key] = report[key][:10]


def fit(result):
    """Keep the payload under the 60 KiB bound, degrading transparently."""
    size = measured(result)
    if size <= MAX_JSON_BYTES:
        return result, size, False
    cited = {item['evidence_id'] for item in result['findings'] if item.get('evidence_id')}
    result['evidence'] = [item for item in result['evidence'] if item['id'] in cited]
    def trim_findings():
        result['findings'] = result['findings'][:20]

    def clip_evidence():
        for item in result['evidence']:
            item['text'] = clip(item['text'], 100)

    def drop_evidence():
        result['evidence'] = result['evidence'][:40]

    def drop_report():
        result['report'] = {'inputs': result['report'].get('inputs', {}),
                            'omitted': 'The structured report exceeded the 60 KiB response bound and was dropped. Re-run with fewer files or one narrower question.'}

    for step in (lambda: shorten_report_lists(result), trim_findings, clip_evidence, drop_evidence, drop_report):
        step()
        size = measured(result)
        if size <= MAX_JSON_BYTES:
            break
    return result, size, True


def analyze(operation, payload):
    """Run one bounded static analysis over a payload prepared by engineering_evidence.

    Raises ValueError for an unsupported operation or a payload that
    engineering_evidence.prepare rejects; the rejection code is its own.
    """
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ValueError('unsupported_operation')
    prepared = evidence_module().prepare(payload)
    if not isinstance(prepared, dict):
        raise ValueError('invalid_evidence_contract')
    ledger = Ledger(prepared.get('evidence'))
    units, warnings = file_units(prepared.get('files'))
    units += parse_diff(prepared.get('diff') or '')
    logs = log_units(prepared)
    inputs = inputs_block(prepared, units, logs, warnings)
    limitations = list(BASE_LIMITATIONS) + list(OPERATION_LIMITATIONS[operation])
    missing = []
    if operation == 'code_review':
        if not units:
            missing = list(CODE_REVIEW_QUESTIONS)
            findings, positives = [], []
        else:
            findings, positives = review_findings(units, ledger)
        findings, capped = order_findings(findings)
        report = code_review_report(findings, positives, units, inputs, missing)
    elif operation == 'debug':
        if not logs:
            missing = list(DEBUG_QUESTIONS)
        log_facts = analyze_logs(logs)
        report = debug_report(prepared, log_facts, [unit for unit in units if unit.source == 'file'], ledger, inputs, missing)
        findings, capped = order_findings(debug_findings(log_facts, report, ledger))
    elif operation == 'testing_strategy':
        structures = [structure(unit) for unit in units]
        report, gaps = testing_report(structures, prepared, ledger, inputs)
        findings, capped = order_findings(gaps)
        if not units:
            report['scope']['note'] = 'No source was supplied, so the plan is derived from the request text only and names no units.'
            limitations.append('No source was supplied: the plan is requirement-derived, lists no real unit and cannot identify gaps.')
    else:
        structures = [structure(unit) for unit in units]
        report, gaps = documentation_report(structures, prepared, ledger, inputs)
        findings, capped = order_findings([
            finding(ledger, item.get('source', 'file'), item['path'], item['line'], item.get('text', item['item']),
                    'undocumented_public_item', 'documentation', 'low',
                    'observation', 'Undocumented: %s' % item['item'],
                    'Document what it does, its inputs and its failure modes, next to the declaration.', item['gap'])
            for item in gaps])
        if not units:
            limitations.append('No source was supplied: the outline is request-derived and every section needs grounding before publication.')
    if capped:
        limitations.append('More findings matched than the %d returned; the list is ordered by severity, so lower-severity matches were dropped.' % MAX_FINDINGS)
    if ledger.capped:
        limitations.append('The evidence list reached its bound of %d items; some cited lines were not included.' % MAX_EVIDENCE)
    if inputs['line_limit_reached']:
        limitations.append('Only the first %d lines of %s were analysed.' % (MAX_LINES_PER_SOURCE, ', '.join(inputs['line_limit_reached'][:5])))
    if inputs['upstream_truncated']:
        limitations.append('Preparation reported truncation: its evidence excerpt reached its own bound. Analysis still ran over the supplied text, and citations past that bound were registered by this analyzer.')
    if inputs['redaction_count']:
        limitations.append('%d value(s) were redacted before analysis, so the original text at those positions was not analysed.' % inputs['redaction_count'])
    degraded = bool(capped or ledger.capped or inputs['line_limit_reached'] or inputs['analyzer_warnings'])
    status = 'clarification_required' if missing else ('degraded' if degraded else 'completed')
    result = {'schema_version': SCHEMA_VERSION, 'operation': operation, 'status': status, 'method': dict(METHOD),
              'evidence': ledger.records(), 'findings': findings, 'report': report, 'limitations': limitations}
    result, _, trimmed = fit(result)
    if trimmed:
        result['status'] = 'clarification_required' if missing else 'degraded'
        result['limitations'].append('The response exceeded the %d byte bound and was reduced; some evidence, findings or report detail was dropped.' % MAX_JSON_BYTES)
    return result


def main(argv=None, stdin=None):
    """Read one JSON payload from stdin and print the analysis. No path is read."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0] not in OPERATIONS:
        raise ValueError('usage: engineering_analyzers.py {%s} < payload.json' % '|'.join(OPERATIONS))
    reader = sys.stdin if stdin is None else stdin
    return analyze(argv[0], json.loads(reader.read()))


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(json.dumps({'error': type(error).__name__, 'detail': str(error)[:200]}), file=sys.stderr)
        sys.exit(2)
