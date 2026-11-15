"""Bounded content samples; mechanics never receive arbitrary message text."""
import re

VERSION = 'quality-redaction-v1'
SECRET = re.compile(r'(?i)(?:\bBearer\s+\S+|\b(?:sk-proj-|sk-|hai_live_|hst_|hgw_|ghp_|github_pat_)[A-Za-z0-9_+./=-]*|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)')
PEM = re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)', re.S)
ASSIGNMENT = re.compile(r'''(?ix)(\b(?:password|passwd|passphrase|pwd|api[_ -]?key|secret(?:[_ -]?key)?|access[_ -]?token|authorization|cookie|set-cookie)\b\s*["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)''')
URL_CREDENTIAL = re.compile(r'(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@')
QUERY_CREDENTIAL = re.compile(r'(?i)([?&](?:token|key|api_key|apikey|secret|password|signature|sig|access_token)=)[^\s&#]+')
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
NUMBER = re.compile(r'(?<!\w)\+?\d[\d ()-]{6,}\d(?!\w)')
MEDIA = re.compile(r'(?i)data:[^\s;,]{1,100}(?:;[^,\s]{1,100})?,[^\s"\']+')


def sanitize(text, limit):
    if not isinstance(text, str):
        raise ValueError('invalid_sample')
    # Limit work before regex processing; reject oversized input rather than scan it.
    if len(text) > 262144:
        raise ValueError('sample_too_large')
    count = 0
    for expression, replacement in (
        (PEM, '[PRIVATE_KEY_REDACTED]'), (MEDIA, '[MEDIA_OMITTED]'),
        (SECRET, '[CREDENTIAL_REDACTED]'), (URL_CREDENTIAL, r'\1[REDACTED]@'),
        (QUERY_CREDENTIAL, r'\1[REDACTED]'), (ASSIGNMENT, r'\1[REDACTED]'),
        (EMAIL, '[EMAIL_REDACTED]'), (IPV4, '[IP_REDACTED]'),
        (NUMBER, '[NUMBER_REDACTED]'),
    ):
        text, changed = expression.subn(replacement, text)
        count += changed
    text = ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)
    return {'text': text[:limit], 'truncated': len(text) > limit,
            'redactions': count, 'redaction_version': VERSION}
