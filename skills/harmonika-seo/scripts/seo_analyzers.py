#!/usr/bin/env python3
"""Deterministic local SEO analysis over caller-supplied fetch results.

Standard library only (Python 3.9+). This module performs no file writes, opens
no sockets and calls no model/LLM provider. Every byte it inspects arrives
either in `payload` or from the `fetcher` callable the caller injects; the
fetcher is SSRF/allowlist guarded by its owning component, not here.

All remote bytes are untrusted input. Extracted strings are sanitised and
truncated before they are placed in evidence, and are never interpreted as
instructions, markup or code. The module reports observed facts and rule-based
findings only: it never produces rankings, traffic, visit counts, difficulty
numbers, authority metrics or a composite "SEO score", because none of those
are derivable from the inputs it is given.

Analysis is deterministic: no clock, no randomness, no network discovery and no
dependency on dict/set iteration order. The same inputs always yield the same
result dict.
"""
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import json
import re
import unicodedata
import xml.etree.ElementTree as ElementTree

SCHEMA_VERSION = 1

OPERATIONS = (
    'seo.page_audit', 'seo.technical', 'seo.content', 'seo.schema',
    'seo.sitemap', 'seo.local', 'seo.geo', 'seo.keyword_cluster',
)

# Bounds. Every limit is explicit so that a hostile or huge response cannot
# turn an audit into unbounded work or unbounded output.
MAX_URL_CHARS = 2048
MAX_HTML_CHARS = 2000000
MAX_TEXT_CHARS = 400000
MAX_HEADINGS = 500
MAX_ANCHORS = 2000
MAX_IMAGES = 1000
MAX_METAS = 300
MAX_LINK_TAGS = 300
MAX_JSONLD_BLOCKS = 50
MAX_JSONLD_CHARS = 200000
MAX_XML_CHARS = 8000000
MAX_SITEMAP_URLS = 2000
MAX_SITEMAP_FETCHES = 4
MAX_TECHNICAL_FETCHES = 4
MAX_GEO_FETCHES = 2
MAX_ROBOTS_CHARS = 500000
MAX_ROBOTS_LINES = 5000
MAX_KEYWORDS = 50
MAX_SERP_URLS = 10
MAX_KEYWORD_CHARS = 200
EVIDENCE_CHARS = 300
MAX_SAMPLES = 10

# Length guidance used only to flag "outside common guidance", never as a score.
TITLE_MIN_CHARS = 15
TITLE_MAX_CHARS = 60
DESCRIPTION_MIN_CHARS = 50
DESCRIPTION_MAX_CHARS = 160
THIN_CONTENT_WORDS = 300
SERP_OVERLAP_THRESHOLD = 0.3
LEXICAL_OVERLAP_THRESHOLD = 0.5

_SEVERITY_RANK = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')
_SPACES = re.compile(r'\s+')
_WORD = re.compile(r'[^\W_]+', re.UNICODE)
_DATE_PREFIX = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')
_PHONE = re.compile(r'(?:\+|\b00)?[\d][\d\s().\-]{6,20}\d')
_POSTCODE = re.compile(r'\b\d{4,6}\b')
_STREET_HINT = re.compile(
    r'\b(?:jl\.?|jalan|gang|gg\.?|street|st\.?|avenue|ave\.?|road|rd\.?|'
    r'boulevard|blvd\.?|lane|drive|suite|floor|lantai|gedung|no\.?)\b',
    re.IGNORECASE)
_XML_DECLARATION = re.compile(r'<!(?:DOCTYPE|ENTITY|ATTLIST|NOTATION)', re.IGNORECASE)


class SeoError(ValueError):
    """Stable machine code plus a customer-safe message without input values."""

    def __init__(self, code, message):
        ValueError.__init__(self, code + ': ' + message)
        self.code = code
        self.message = message


def _fail(code, message):
    raise SeoError(code, message)


def _clean(value, limit=EVIDENCE_CHARS):
    """Collapse and bound one untrusted string for safe inclusion in evidence."""
    if not isinstance(value, str):
        return ''
    text = _SPACES.sub(' ', _CONTROL.sub(' ', value)).strip()
    if len(text) > limit:
        return text[:limit] + '...[truncated]'
    return text


def _samples(values, limit=MAX_SAMPLES):
    """Bounded, order-preserving sample plus the count that was left out."""
    items = list(values)
    return {'sample': items[:limit], 'total': len(items),
            'sample_truncated': len(items) > limit}


def _finding(severity, code, evidence, recommendation):
    if severity not in _SEVERITY_RANK:
        _fail('invalid_severity', 'Finding severity is not a known level')
    return {'severity': severity, 'code': code, 'evidence': evidence,
            'recommendation': recommendation}


def _ordered(findings):
    return sorted(findings, key=lambda item: (_SEVERITY_RANK[item['severity']], item['code']))


def _result(operation, status, method, evidence, findings, limitations):
    return {'schema_version': SCHEMA_VERSION, 'operation': operation, 'status': status,
            'method': method, 'evidence': evidence, 'findings': _ordered(findings),
            'limitations': list(limitations)}


# --------------------------------------------------------------------------
# Boundary validation. The root registry validates first; these checks make the
# function safe to call directly, from tests and from any other caller.
# --------------------------------------------------------------------------

def _require_operation(operation):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        _fail('unknown_operation', 'The requested SEO operation is not implemented')
    return operation


def _require_payload(payload, allowed, required):
    if not isinstance(payload, dict):
        _fail('invalid_payload', 'Payload must be a JSON object')
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        _fail('unexpected_field', 'Payload contains fields this operation does not accept')
    for key in required:
        if key not in payload:
            _fail('missing_field', 'Payload is missing a required field: ' + key)
    return payload


def _require_url(value):
    if not isinstance(value, str) or not value.strip():
        _fail('invalid_url', 'url must be a non-empty string')
    url = value.strip()
    if len(url) > MAX_URL_CHARS:
        _fail('invalid_url', 'url exceeds the maximum accepted length')
    if _CONTROL.search(url) or any(char.isspace() for char in url):
        _fail('invalid_url', 'url must not contain control or whitespace characters')
    parts = urlsplit(url)
    if parts.scheme.lower() not in ('http', 'https'):
        _fail('unsupported_scheme', 'url must use the http or https scheme')
    if not parts.hostname:
        _fail('invalid_url', 'url must include a host')
    return url


def _require_fetcher(fetcher):
    if not callable(fetcher):
        _fail('invalid_fetcher', 'A callable fetcher is required for remote operations')
    return fetcher


def _host(url):
    try:
        return (urlsplit(url).hostname or '').lower().rstrip('.').encode('idna').decode('ascii')
    except ValueError:
        return ''


def _same_site(first, second):
    """Exact host match, ignoring a single leading ``www.``.

    The standard library carries no public suffix list, so this deliberately
    does not compute eTLD+1; ``a.example.com`` and ``b.example.com`` are
    reported as different hosts rather than guessed to be the same site.
    """
    left, right = _host(first), _host(second)
    if not left or not right:
        return False
    for prefix in ('www.',):
        left = left[len(prefix):] if left.startswith(prefix) else left
        right = right[len(prefix):] if right.startswith(prefix) else right
    return left == right


# --------------------------------------------------------------------------
# Fetch handling. One wrapper, one budget, per-error degradation.
# --------------------------------------------------------------------------

def _new_state(budget):
    return {'fetches': 0, 'budget': budget, 'errors': [], 'fetched': [],
            'consumed': set()}


def _normalize_response(raw, requested):
    if not isinstance(raw, dict):
        _fail('fetch_contract', 'fetcher must return a mapping')
    url = raw.get('url')
    url = url if isinstance(url, str) and url else requested
    status = raw.get('status')
    status = status if isinstance(status, int) and not isinstance(status, bool) else 0
    headers = {}
    source = raw.get('headers')
    if isinstance(source, dict):
        for key in sorted(key for key in source if isinstance(key, str)):
            value = source[key]
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                headers[key.lower().strip()] = _clean(str(value), 1024)
    text = raw.get('text')
    text = text if isinstance(text, str) else ''
    truncated = len(text) > MAX_HTML_CHARS
    content_type = raw.get('content_type')
    if not isinstance(content_type, str):
        content_type = headers.get('content-type', '')
    return {'url': url, 'status': status, 'headers': headers,
            'text': text[:MAX_HTML_CHARS], 'body_truncated': truncated,
            'content_type': _clean(content_type, 200).lower()}


def _fetch(fetcher, url, state):
    """Fetch one URL inside the budget; never raise, record and degrade."""
    if state['fetches'] >= state['budget']:
        state['errors'].append({'url': _clean(url), 'reason': 'fetch_budget_exhausted'})
        return None
    state['fetches'] += 1
    try:
        raw = fetcher(url)
    except Exception as error:  # noqa: BLE001 - caller-supplied fetcher, degrade per error
        state['errors'].append({'url': _clean(url), 'reason': 'fetch_failed',
                                'error_type': type(error).__name__})
        return None
    try:
        response = _normalize_response(raw, url)
    except SeoError as error:
        state['errors'].append({'url': _clean(url), 'reason': error.code})
        return None
    state['fetched'].append({'url': _clean(response['url']), 'status': response['status'],
                             'content_type': response['content_type']})
    return response


def _consume_error(state, url):
    """Mark a URL's fetch errors as already reported with a specific finding."""
    state['consumed'].add(url)


def _fetch_errors_to_findings(state):
    """Generic findings for fetch errors no operation reported specifically."""
    findings = []
    for item in state['errors']:
        if item['url'] in state['consumed']:
            continue
        reason = item['reason']
        if reason == 'fetch_budget_exhausted':
            findings.append(_finding(
                'info', 'fetch_budget_exhausted', dict(item),
                'This operation stops at its documented fetch budget; request the '
                'remaining URLs in a separate run.'))
        else:
            findings.append(_finding(
                'medium', 'fetch_unavailable', dict(item),
                'The URL could not be retrieved, so checks depending on it were '
                'skipped rather than guessed. Confirm the URL is reachable and allowed.'))
    return findings


# --------------------------------------------------------------------------
# HTML fact extraction.
# --------------------------------------------------------------------------

_SKIP_TAGS = frozenset(('script', 'style', 'noscript', 'template', 'svg', 'math',
                        'iframe', 'object', 'canvas', 'title'))
_BREAK_TAGS = frozenset(('p', 'div', 'br', 'li', 'tr', 'td', 'th', 'section', 'article',
                         'header', 'footer', 'nav', 'aside', 'main', 'figure', 'figcaption',
                         'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                         'ul', 'ol', 'dl', 'dt', 'dd', 'table', 'form', 'hr', 'address'))
_HEADINGS = frozenset(('h1', 'h2', 'h3', 'h4', 'h5', 'h6'))
_AUTHOR_META = ('author', 'article:author', 'og:article:author', 'dc.creator',
                'dcterms.creator', 'twitter:creator', 'byl')
_DATE_META = ('article:published_time', 'article:modified_time', 'og:updated_time',
              'date', 'dc.date', 'dcterms.date', 'dcterms.modified',
              'last-modified', 'pubdate', 'publish_date')


class _HtmlFacts(HTMLParser):
    """Tolerant extraction of the page facts the SEO rules need.

    Text inside script/style/noscript/template/svg/math/iframe/object/canvas and
    the document title is excluded from visible text, matching what a reader
    actually sees. Duplicate attributes keep their first value, as browsers do.
    """

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.titles = []
        self.metas = []
        self.link_tags = []
        self.headings = []
        self.anchors = []
        self.images = []
        self.jsonld_blocks = []
        self.itemtypes = []
        self.lang = None
        self.time_values = []
        self.counts = {'list': 0, 'list_item': 0, 'table': 0, 'paragraph': 0,
                       'jsonld_script': 0, 'other_ld_script': 0, 'form': 0}
        self.truncated = {}
        self.parse_error = None
        self._text = []
        self._text_chars = 0
        self._skip = []
        self._title = None
        self._heading = None
        self._anchor = None
        self._ld = None
        self._ld_chars = 0

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _attrs(attrs):
        merged = {}
        for key, value in attrs:
            if not isinstance(key, str):
                continue
            key = key.lower()
            if key not in merged:
                merged[key] = value if isinstance(value, str) else ''
        return merged

    def _add_text(self, data):
        if self._text_chars >= MAX_TEXT_CHARS:
            self.truncated['visible_text'] = True
            return
        room = MAX_TEXT_CHARS - self._text_chars
        chunk = data[:room]
        if len(data) > room:
            self.truncated['visible_text'] = True
        self._text.append(chunk)
        self._text_chars += len(chunk)

    def _append(self, bucket, limit, item, label):
        if len(bucket) >= limit:
            self.truncated[label] = True
            return
        bucket.append(item)

    def _close_heading(self):
        if self._heading is not None:
            level, parts = self._heading
            self._append(self.headings, MAX_HEADINGS,
                         {'level': level, 'text': _clean(''.join(parts))}, 'headings')
            self._heading = None

    def _close_anchor(self):
        if self._anchor is not None:
            anchor = self._anchor
            anchor['text'] = _clean(''.join(anchor['text']), 200)
            self._append(self.anchors, MAX_ANCHORS, anchor, 'anchors')
            self._anchor = None

    def _close_ld(self):
        if self._ld is not None:
            self._append(self.jsonld_blocks, MAX_JSONLD_BLOCKS, ''.join(self._ld), 'jsonld')
            self._ld = None

    # -- HTMLParser hooks ------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = self._attrs(attrs)
        if tag == 'html' and self.lang is None:
            self.lang = _clean(attributes.get('lang', ''), 32) or None
        elif tag == 'meta':
            self._handle_meta(attributes)
        elif tag == 'link':
            rel = _clean(attributes.get('rel', ''), 64).lower()
            self._append(self.link_tags, MAX_LINK_TAGS,
                         {'rel': rel, 'href': _clean(attributes.get('href', ''), MAX_URL_CHARS),
                          'hreflang': _clean(attributes.get('hreflang', ''), 32)}, 'link_tags')
        elif tag == 'img':
            self._append(self.images, MAX_IMAGES,
                         {'src': _clean(attributes.get('src', ''), MAX_URL_CHARS),
                          'has_alt': 'alt' in attributes,
                          'alt': _clean(attributes.get('alt', ''), 200),
                          'decorative_role': attributes.get('role', '').lower() == 'presentation'},
                         'images')
        elif tag == 'title':
            self._title = []
        elif tag == 'script':
            kind = attributes.get('type', '').lower().split(';')[0].strip()
            if kind == 'application/ld+json':
                self.counts['jsonld_script'] += 1
                self._ld = []
                self._ld_chars = 0
            elif kind in ('application/json+ld', 'application/ld json'):
                self.counts['other_ld_script'] += 1
        elif tag == 'a':
            self._close_anchor()
            self._anchor = {'href': _clean(attributes.get('href', ''), MAX_URL_CHARS),
                            'rel': _clean(attributes.get('rel', ''), 64).lower(),
                            'text': []}
        elif tag == 'time':
            value = _clean(attributes.get('datetime', ''), 64)
            if value:
                self._append(self.time_values, MAX_SAMPLES * 4,
                             {'source': 'time[datetime]', 'value': value}, 'time_values')
        elif tag in _HEADINGS:
            self._close_heading()
            self._heading = (int(tag[1]), [])
        elif tag in ('ul', 'ol'):
            self.counts['list'] += 1
        elif tag == 'li':
            self.counts['list_item'] += 1
        elif tag == 'table':
            self.counts['table'] += 1
        elif tag == 'p':
            self.counts['paragraph'] += 1
        elif tag == 'form':
            self.counts['form'] += 1
        if 'itemtype' in attributes:
            self._append(self.itemtypes, MAX_SAMPLES * 4,
                         _clean(attributes['itemtype'], 200), 'itemtypes')
        if tag in _SKIP_TAGS:
            self._skip.append(tag)
        elif tag in _BREAK_TAGS:
            self._add_text(' ')

    def _handle_meta(self, attributes):
        key = ''
        kind = ''
        for candidate in ('name', 'property', 'http-equiv', 'itemprop'):
            if attributes.get(candidate):
                key, kind = attributes[candidate], candidate
                break
        if not key and 'charset' in attributes:
            key, kind = 'charset', 'charset'
        self._append(self.metas, MAX_METAS,
                     {'key': _clean(key, 128).lower(), 'kind': kind,
                      'content': _clean(attributes.get('content', '') or
                                        attributes.get('charset', ''), 1024)}, 'metas')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'title' and self._title is not None:
            self.titles.append(_clean(''.join(self._title)))
            self._title = None
        elif tag == 'script':
            self._close_ld()
        elif tag == 'a':
            self._close_anchor()
        elif tag in _HEADINGS:
            self._close_heading()
        if tag in _SKIP_TAGS and tag in self._skip:
            while self._skip:
                if self._skip.pop() == tag:
                    break
        elif tag in _BREAK_TAGS:
            self._add_text(' ')

    def handle_data(self, data):
        if not isinstance(data, str) or not data:
            return
        if self._ld is not None:
            room = MAX_JSONLD_CHARS - self._ld_chars
            if room > 0:
                self._ld.append(data[:room])
                self._ld_chars += min(room, len(data))
            else:
                self.truncated['jsonld_block'] = True
        if self._title is not None:
            self._title.append(data)
        if self._skip:
            return
        if self._heading is not None:
            self._heading[1].append(data)
        if self._anchor is not None:
            self._anchor['text'].append(data)
        self._add_text(data)

    # -- output ----------------------------------------------------------
    def finish(self):
        self._close_heading()
        self._close_anchor()
        self._close_ld()
        if self._title is not None:
            self.titles.append(_clean(''.join(self._title)))
            self._title = None
        return self

    @property
    def visible_text(self):
        return _SPACES.sub(' ', ''.join(self._text)).strip()

    def meta_values(self, key):
        return [item['content'] for item in self.metas if item['key'] == key]

    def meta_count(self, key):
        return sum(1 for item in self.metas if item['key'] == key)

    def links_by_rel(self, rel):
        return [item for item in self.link_tags if rel in item['rel'].split()]


def parse_html(text):
    """Parse untrusted HTML into bounded facts; malformed markup degrades, never raises."""
    facts = _HtmlFacts()
    if not isinstance(text, str):
        text = ''
    try:
        facts.feed(text[:MAX_HTML_CHARS])
        facts.close()
    except Exception as error:  # noqa: BLE001 - tolerate any malformed-markup failure
        facts.parse_error = type(error).__name__
    return facts.finish()


# --------------------------------------------------------------------------
# Rule checks. Each returns a list of findings and never mutates its inputs.
# --------------------------------------------------------------------------

def check_response(response, requested):
    findings = []
    if not 200<=response['status']<300:
        findings.append(_finding(
            'high', 'http_status_not_ok',
            {'status': response['status'], 'url': _clean(response['url'])},
            'Investigate unsuccessful responses or unresolved redirects before '
            'treating the remaining findings as representative.'))
    kind = response['content_type'].split(';')[0].strip()
    if kind and kind not in ('text/html', 'application/xhtml+xml'):
        findings.append(_finding(
            'medium', 'content_type_not_html', {'content_type': kind},
            'HTML checks assume an HTML content type; verify the server sends '
            'text/html for this URL.'))
    if urlsplit(requested).scheme.lower() != 'https':
        findings.append(_finding(
            'medium', 'url_not_https', {'scheme': urlsplit(requested).scheme.lower()},
            'Serve the page over HTTPS and redirect the HTTP form to it.'))
    if response['body_truncated']:
        findings.append(_finding(
            'info', 'response_body_truncated', {'limit_chars': MAX_HTML_CHARS},
            'Only the first portion of the response was analysed; findings cover '
            'that portion.'))
    if response.get('parse_error'):
        findings.append(_finding(
            'medium', 'html_parse_degraded', {'error_type': response['parse_error']},
            'The markup could not be fully parsed. Findings cover only what parsed.'))
    return findings


def check_title(facts):
    findings = []
    titles = [item for item in facts.titles if item]
    if not facts.titles:
        findings.append(_finding(
            'high', 'title_missing', {'title_tags': 0},
            'Add a single descriptive <title> element in the document head.'))
    elif not titles:
        findings.append(_finding(
            'high', 'title_empty', {'title_tags': len(facts.titles)},
            'The <title> element is present but empty; give it descriptive text.'))
    if len(facts.titles) > 1:
        findings.append(_finding(
            'medium', 'title_duplicate_tags',
            {'title_tags': len(facts.titles),
             'values': _samples([_clean(x, 120) for x in facts.titles])},
            'Keep exactly one <title> element; remove the duplicates.'))
    for value in titles[:1]:
        length = len(value)
        if length > TITLE_MAX_CHARS or length < TITLE_MIN_CHARS:
            findings.append(_finding(
                'low', 'title_length_outside_guidance',
                {'chars': length, 'guidance_min': TITLE_MIN_CHARS,
                 'guidance_max': TITLE_MAX_CHARS, 'value': _clean(value, 120)},
                'Common guidance is roughly %d-%d characters so the title is not '
                'truncated in results. This is a length observation, not a ranking '
                'prediction.' % (TITLE_MIN_CHARS, TITLE_MAX_CHARS)))
    return findings


def check_meta_description(facts):
    findings = []
    values = [item for item in facts.meta_values('description') if item]
    count = facts.meta_count('description')
    if count == 0:
        findings.append(_finding(
            'medium', 'meta_description_missing', {'meta_description_tags': 0},
            'Add one meta description summarising the page; search engines may '
            'still generate their own snippet.'))
    elif not values:
        findings.append(_finding(
            'medium', 'meta_description_empty', {'meta_description_tags': count},
            'The meta description is present but empty; give it summary text.'))
    if count > 1:
        findings.append(_finding(
            'medium', 'meta_description_duplicate_tags',
            {'meta_description_tags': count, 'values': _samples([_clean(x, 180) for x in values])},
            'Keep exactly one meta description tag; remove the duplicates.'))
    for value in values[:1]:
        length = len(value)
        if length > DESCRIPTION_MAX_CHARS or length < DESCRIPTION_MIN_CHARS:
            findings.append(_finding(
                'low', 'meta_description_length_outside_guidance',
                {'chars': length, 'guidance_min': DESCRIPTION_MIN_CHARS,
                 'guidance_max': DESCRIPTION_MAX_CHARS},
                'Common guidance is roughly %d-%d characters. Snippet length is '
                'chosen by the search engine, so treat this as a length observation.'
                % (DESCRIPTION_MIN_CHARS, DESCRIPTION_MAX_CHARS)))
    titles = [item for item in facts.titles if item]
    if titles and values and titles[0].casefold() == values[0].casefold():
        findings.append(_finding(
            'low', 'title_and_description_identical', {'value': _clean(titles[0], 120)},
            'Give the title and meta description distinct text so each describes a '
            'different aspect of the page.'))
    return findings


def check_headings(facts):
    findings = []
    levels = [item['level'] for item in facts.headings]
    h1s = [item['text'] for item in facts.headings if item['level'] == 1]
    if not facts.headings:
        findings.append(_finding(
            'medium', 'headings_absent', {'headings': 0},
            'Add a heading outline (at least one h1) so the page structure is explicit.'))
    elif not h1s:
        findings.append(_finding(
            'medium', 'h1_missing', {'headings': len(facts.headings),
                                     'levels_present': sorted(set(levels))},
            'Add one h1 that states the page topic.'))
    if len(h1s) > 1:
        findings.append(_finding(
            'low', 'h1_multiple', {'h1_count': len(h1s),
                                   'values': _samples([_clean(x, 120) for x in h1s])},
            'Prefer a single h1 per page and use h2/h3 for subsections.'))
    if levels and levels[0] > 1:
        findings.append(_finding(
            'low', 'heading_outline_starts_below_h1',
            {'first_heading_level': levels[0]},
            'Start the outline at h1 so the document hierarchy begins at the top level.'))
    previous = 0
    for index, level in enumerate(levels):
        if previous and level > previous + 1:
            findings.append(_finding(
                'low', 'heading_order_skips_level',
                {'position': index, 'from_level': previous, 'to_level': level},
                'Avoid skipping heading levels; step down one level at a time so the '
                'outline stays machine-readable.'))
            break
        previous = level
    empty = sum(1 for item in facts.headings if not item['text'])
    if empty:
        findings.append(_finding(
            'low', 'heading_empty', {'empty_headings': empty},
            'Remove or fill headings that contain no text.'))
    return findings


def check_canonical(facts, requested):
    findings = []
    tags = facts.links_by_rel('canonical')
    hrefs = [item['href'] for item in tags if item['href']]
    if not tags:
        findings.append(_finding(
            'low', 'canonical_missing', {'canonical_tags': 0},
            'Add a self-referencing rel=canonical link unless this URL should be '
            'canonicalised elsewhere.'))
    if len(tags) > 1:
        findings.append(_finding(
            'medium', 'canonical_multiple',
            {'canonical_tags': len(tags), 'values': _samples(hrefs)},
            'Conflicting canonical links are ignored by search engines; keep one.'))
    for href in hrefs[:1]:
        parts = urlsplit(href)
        if not parts.scheme or not parts.netloc:
            findings.append(_finding(
                'low', 'canonical_not_absolute', {'value': _clean(href),
                                                  'resolved': _clean(urljoin(requested, href))},
                'Use a fully qualified absolute canonical URL.'))
        resolved = urljoin(requested, href)
        if not _same_site(resolved, requested):
            findings.append(_finding(
                'medium', 'canonical_points_to_other_host',
                {'canonical_host': _host(resolved), 'page_host': _host(requested),
                 'note': 'host comparison only; eTLD+1 is not computed'},
                'A cross-host canonical asks search engines to index the other URL '
                'instead. Confirm that is intended.'))
    return findings


def check_indexability(facts, response):
    findings = []
    directives = []
    for key in ('robots', 'googlebot'):
        for value in facts.meta_values(key):
            directives.extend(part.strip().lower() for part in value.split(',') if part.strip())
    header = response['headers'].get('x-robots-tag', '')
    header_directives = [part.strip().lower() for part in header.split(',') if part.strip()]
    if 'noindex' in directives:
        findings.append(_finding(
            'high', 'meta_robots_noindex', {'directives': _samples(sorted(set(directives)))},
            'This page asks search engines not to index it. Remove the noindex '
            'directive if the page should appear in search results.'))
    if 'nofollow' in directives:
        findings.append(_finding(
            'medium', 'meta_robots_nofollow', {'directives': _samples(sorted(set(directives)))},
            'A page-level nofollow stops link discovery from this page; confirm it is intended.'))
    if 'noindex' in header_directives:
        findings.append(_finding(
            'high', 'x_robots_tag_noindex', {'header': _clean(header, 200)},
            'The X-Robots-Tag response header blocks indexing. Remove it if the page '
            'should be indexable.'))
    return findings, {'meta_robots': sorted(set(directives)),
                      'x_robots_tag': sorted(set(header_directives))}


def check_images(facts):
    findings = []
    missing = [item['src'] for item in facts.images if not item['has_alt']]
    if missing:
        findings.append(_finding(
            'medium', 'images_missing_alt_attribute',
            {'images_total': len(facts.images), 'missing_alt': len(missing),
             'values': _samples(missing)},
            'Give every content image an alt attribute; use alt="" only for purely '
            'decorative images.'))
    return findings


def check_document_shell(facts):
    findings = []
    if not facts.lang:
        findings.append(_finding(
            'low', 'html_lang_missing', {'lang': None},
            'Set a lang attribute on <html> so language is unambiguous.'))
    if not facts.meta_values('viewport'):
        findings.append(_finding(
            'low', 'viewport_meta_missing', {'viewport_tags': facts.meta_count('viewport')},
            'Add a viewport meta tag so the page adapts to mobile viewports.'))
    return findings


def content_metrics(facts):
    text = facts.visible_text
    words = _WORD.findall(text)
    outline = [{'level': item['level'], 'text': item['text']} for item in facts.headings]
    return {'word_count': len(words),
            'visible_text_chars': len(text),
            'visible_text_excerpt': _clean(text, EVIDENCE_CHARS),
            'heading_outline': _samples(outline, MAX_SAMPLES * 2),
            'paragraphs': facts.counts['paragraph'],
            'lists': facts.counts['list'],
            'list_items': facts.counts['list_item'],
            'tables': facts.counts['table'],
            'links_total': len(facts.anchors),
            'images_total': len(facts.images),
            'truncated': dict(facts.truncated),
            'counting_rule': 'unicode word characters in visible text, excluding '
                             'script/style/noscript/template/svg/iframe and the title'}


def check_content(metrics):
    findings = []
    if metrics['word_count'] < THIN_CONTENT_WORDS:
        findings.append(_finding(
            'low', 'visible_word_count_low',
            {'word_count': metrics['word_count'], 'threshold': THIN_CONTENT_WORDS,
             'note': 'local threshold, not a quality judgement or ranking signal'},
            'Check whether the page has enough substantive visible text for its '
            'purpose. Word count alone does not determine quality; some pages are '
            'legitimately short.'))
    if not metrics['heading_outline']['total']:
        findings.append(_finding(
            'low', 'heading_outline_empty', {'headings': 0},
            'Add headings so long-form content can be skimmed and parsed.'))
    return findings


def authorship_evidence(facts, jsonld_nodes):
    """Collect observable authorship/date signals. These are evidence labels only.

    This is explicitly NOT an E-E-A-T score. Experience, expertise,
    authoritativeness and trust are not machine-measurable from one page's
    markup; presence of a byline does not establish expertise and absence does
    not establish its lack.
    """
    authors = []
    for key in _AUTHOR_META:
        for value in facts.meta_values(key):
            if value:
                authors.append({'source': 'meta[' + key + ']', 'value': _clean(value, 120)})
    for link in facts.link_tags:
        if 'author' in link['rel'].split() and link['href']:
            authors.append({'source': 'link[rel=author]', 'value': link['href']})
    dates = list(facts.time_values)
    for key in _DATE_META:
        for value in facts.meta_values(key):
            if value:
                dates.append({'source': 'meta[' + key + ']', 'value': _clean(value, 64)})
    for node in jsonld_nodes:
        for key in ('author', 'creator'):
            value = node.get(key)
            label = _jsonld_name(value)
            if label:
                authors.append({'source': 'jsonld[' + key + ']', 'value': label})
        for key in ('datePublished', 'dateModified'):
            value = node.get(key)
            if isinstance(value, str) and value:
                dates.append({'source': 'jsonld[' + key + ']', 'value': _clean(value, 64)})
    for item in dates:
        item['parses_as_date'] = _is_date_like(item['value'])
    return {'author_signals': _samples(authors),
            'date_signals': _samples(dates),
            'interpretation': 'Observed markup signals only. Not an E-E-A-T score and '
                              'not a judgement of author expertise, accuracy or '
                              'trustworthiness.'}


def check_authorship(evidence):
    findings = []
    if not evidence['author_signals']['total']:
        findings.append(_finding(
            'info', 'authorship_markup_absent', {'author_signals': 0},
            'No author markup was found. For content where authorship matters, '
            'consider a visible byline plus author markup. Absence of markup is not '
            'itself a quality problem.'))
    if not evidence['date_signals']['total']:
        findings.append(_finding(
            'info', 'date_markup_absent', {'date_signals': 0},
            'No published/modified date markup was found. Add it where content '
            'recency is meaningful to readers.'))
    unparsed = [item for item in evidence['date_signals']['sample']
                if not item['parses_as_date']]
    if unparsed:
        findings.append(_finding(
            'low', 'date_value_not_iso8601', {'values': _samples(unparsed)},
            'Express dates in ISO 8601 (YYYY-MM-DD) so they are unambiguous to parsers.'))
    return findings


def _is_date_like(value):
    match = _DATE_PREFIX.match(value or '')
    if not match:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True


def _jsonld_name(value):
    """Best-effort readable label for an untrusted JSON-LD author/creator value."""
    if isinstance(value, str):
        return _clean(value, 120)
    if isinstance(value, dict):
        name = value.get('name')
        return _clean(name, 120) if isinstance(name, str) else ''
    if isinstance(value, list):
        for item in value:
            label = _jsonld_name(item)
            if label:
                return label
    return ''


# --------------------------------------------------------------------------
# JSON-LD. Syntax parsing plus a small, explicitly partial required-property
# table. This is NOT schema.org validation and NOT Google rich-result
# eligibility: only the listed properties of the listed types are checked.
# --------------------------------------------------------------------------

REQUIRED_PROPERTIES = {
    'Article': ('headline', 'author', 'datePublished'),
    'NewsArticle': ('headline', 'author', 'datePublished'),
    'BlogPosting': ('headline', 'author', 'datePublished'),
    'Product': ('name',),
    'Offer': ('price', 'priceCurrency'),
    'BreadcrumbList': ('itemListElement',),
    'FAQPage': ('mainEntity',),
    'Question': ('name', 'acceptedAnswer'),
    'Organization': ('name',),
    'LocalBusiness': ('name', 'address'),
    'Person': ('name',),
    'WebSite': ('name', 'url'),
    'WebPage': ('name',),
    'Event': ('name', 'startDate', 'location'),
    'Recipe': ('name', 'recipeIngredient', 'recipeInstructions'),
    'VideoObject': ('name', 'thumbnailUrl', 'uploadDate'),
    'JobPosting': ('title', 'datePosted', 'hiringOrganization', 'jobLocation'),
    'HowTo': ('name', 'step'),
    'Review': ('author', 'reviewRating'),
    'AggregateRating': ('ratingValue',),
}

MAX_JSONLD_NODES = 500
MAX_JSONLD_DEPTH = 12


def _jsonld_types(node):
    value = node.get('@type')
    values = value if isinstance(value, list) else [value]
    return sorted({_clean(item, 80) for item in values if isinstance(item, str) and item})


def _flatten_jsonld(value, nodes, depth=0):
    """Collect object nodes from an untrusted JSON-LD tree within fixed bounds."""
    if depth > MAX_JSONLD_DEPTH or len(nodes) >= MAX_JSONLD_NODES:
        return
    if isinstance(value, dict):
        nodes.append(value)
        for key in sorted(value):
            _flatten_jsonld(value[key], nodes, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _flatten_jsonld(item, nodes, depth + 1)


def parse_jsonld(blocks):
    """Parse raw JSON-LD script bodies; return (nodes, parsed docs, findings)."""
    findings = []
    nodes = []
    documents = []
    for index, raw in enumerate(blocks):
        try:
            document = json.loads(raw)
        except (ValueError, TypeError) as error:
            findings.append(_finding(
                'medium', 'jsonld_parse_error',
                {'block_index': index, 'error': _clean(str(error), 160),
                 'excerpt': _clean(raw, 120)},
                'Fix the JSON syntax; a block that does not parse is ignored entirely '
                'by consumers.'))
            continue
        if not isinstance(document, (dict, list)):
            findings.append(_finding(
                'low', 'jsonld_not_object_or_array',
                {'block_index': index, 'json_type': type(document).__name__},
                'A JSON-LD block should be an object or an array of objects.'))
            continue
        documents.append({'block_index': index})
        collected = []
        _flatten_jsonld(document, collected)
        if len(collected) >= MAX_JSONLD_NODES:
            findings.append(_finding(
                'info', 'jsonld_graph_truncated',
                {'block_index': index, 'node_limit': MAX_JSONLD_NODES},
                'Only the first nodes of this graph were inspected.'))
        top = document if isinstance(document, dict) else None
        if top is not None and not any(key in top for key in ('@context', '@graph')):
            findings.append(_finding(
                'low', 'jsonld_context_missing', {'block_index': index},
                'Declare "@context": "https://schema.org" so types resolve.'))
        nodes.extend(collected)
    return nodes[:MAX_JSONLD_NODES], documents, findings


def check_jsonld_properties(nodes):
    findings = []
    typed = 0
    seen_types = []
    untyped = 0
    for node in nodes:
        types = _jsonld_types(node)
        if not types:
            untyped += 1
            continue
        typed += 1
        seen_types.extend(types)
        for name in types:
            required = REQUIRED_PROPERTIES.get(name)
            if not required:
                continue
            missing = [key for key in required if not _has_property(node, key)]
            if missing:
                findings.append(_finding(
                    'medium', 'jsonld_required_property_missing',
                    {'type': name, 'missing': missing, 'checked': list(required),
                     'scope': 'selected properties only; not full schema.org or '
                              'search-engine rich-result validation'},
                    'Add the listed properties for %s, then validate with the '
                    'search engine\'s own structured-data test before relying on '
                    'rich results.' % name))
    return findings, {'nodes': len(nodes), 'typed_nodes': typed,
                      'untyped_nodes': untyped,
                      'types_seen': _samples(sorted(set(seen_types)), MAX_SAMPLES * 2)}


def _has_property(node, key):
    value = node.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


# --------------------------------------------------------------------------
# Sitemap XML. Bounded parsing that refuses DTD/ENTITY constructs outright.
# --------------------------------------------------------------------------

_SITEMAP_CHANGEFREQ = ('always', 'hourly', 'daily', 'weekly', 'monthly', 'yearly', 'never')


def _strip_namespace(tag):
    return tag.split('}', 1)[1] if isinstance(tag, str) and '}' in tag else tag


def parse_sitemap_xml(text):
    """Parse sitemap XML safely. Returns (root_kind, entries, findings).

    Any DOCTYPE/ENTITY/ATTLIST/NOTATION declaration is refused before parsing,
    so entity-expansion and external-entity constructs are never processed.
    """
    findings = []
    if not isinstance(text, str) or not text.strip():
        return None, [], [_finding(
            'high', 'sitemap_body_empty', {'chars': 0},
            'The sitemap response had no body to parse.')]
    if len(text) > MAX_XML_CHARS:
        findings.append(_finding(
            'info', 'sitemap_body_truncated', {'limit_chars': MAX_XML_CHARS},
            'Only the first portion of the sitemap was parsed.'))
        text = text[:MAX_XML_CHARS]
    declaration = _XML_DECLARATION.search(text.replace('\x00',''))
    if declaration:
        return None, [], [_finding(
            'high', 'sitemap_doctype_refused',
            {'construct': _clean(declaration.group(0), 32)},
            'This parser refuses DTD, ENTITY, ATTLIST and NOTATION declarations. '
            'Publish the sitemap without them; they are not part of the sitemap '
            'protocol and enable entity-expansion attacks.')]
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        return None, [], [_finding(
            'high', 'sitemap_xml_parse_error', {'error': _clean(str(error), 160)},
            'Fix the XML syntax; an unparseable sitemap is unusable to crawlers.')]
    kind = _strip_namespace(root.tag)
    if kind not in ('urlset', 'sitemapindex'):
        findings.append(_finding(
            'medium', 'sitemap_root_unexpected', {'root_element': _clean(str(kind), 64)},
            'A sitemap root element must be <urlset> or <sitemapindex>.'))
    entries = []
    child_name = 'sitemap' if kind == 'sitemapindex' else 'url'
    total_seen = 0
    for child in root:
        if _strip_namespace(child.tag) != child_name:
            continue
        total_seen += 1
        if len(entries) >= MAX_SITEMAP_URLS:
            continue
        entry = {}
        for field in child:
            name = _strip_namespace(field.tag)
            if name in ('loc', 'lastmod', 'changefreq', 'priority') and name not in entry:
                entry[name] = _clean(field.text or '', MAX_URL_CHARS)
        entries.append(entry)
    if total_seen > len(entries):
        findings.append(_finding(
            'info', 'sitemap_entries_truncated',
            {'entries_found': total_seen, 'entries_analysed': len(entries),
             'limit': MAX_SITEMAP_URLS},
            'Analysis is capped at %d entries per sitemap; the remainder was counted '
            'but not inspected.' % MAX_SITEMAP_URLS))
    return kind, entries, findings


def check_sitemap_entries(entries, sitemap_url):
    """Validate the analysed entries of one sitemap document.

    `entries` is already capped by parse_sitemap_xml, which reports truncation
    separately, so an empty list here means the document listed nothing.
    """
    findings = []
    locs = [entry.get('loc', '') for entry in entries]
    missing = sum(1 for value in locs if not value)
    if missing:
        findings.append(_finding(
            'medium', 'sitemap_entry_without_loc', {'entries_missing_loc': missing},
            'Every <url>/<sitemap> entry needs a <loc> value.'))
    if not entries:
        findings.append(_finding(
            'medium', 'sitemap_empty', {'entries': 0},
            'The sitemap lists no entries; publish the URLs you want crawled or '
            'remove the empty sitemap.'))
    bad_scheme = []
    cross_host = []
    seen = {}
    duplicates = []
    for value in locs:
        if not value:
            continue
        parts = urlsplit(value)
        if parts.scheme.lower() not in ('http', 'https') or not parts.netloc:
            bad_scheme.append(value)
            continue
        if not _same_site(value, sitemap_url):
            cross_host.append(value)
        if value in seen:
            duplicates.append(value)
        seen[value] = True
    if bad_scheme:
        findings.append(_finding(
            'medium', 'sitemap_loc_not_absolute_http', {'values': _samples(bad_scheme)},
            'Sitemap <loc> values must be absolute http(s) URLs.'))
    if cross_host:
        findings.append(_finding(
            'medium', 'sitemap_loc_other_host',
            {'sitemap_host': _host(sitemap_url), 'values': _samples(cross_host),
             'note': 'host comparison only; eTLD+1 is not computed'},
            'Entries on another host are normally ignored unless cross-submission is '
            'configured. This analyser never follows them.'))
    if duplicates:
        findings.append(_finding(
            'low', 'sitemap_duplicate_loc', {'values': _samples(sorted(set(duplicates))),
                                             'duplicate_entries': len(duplicates)},
            'Remove duplicate <loc> entries.'))
    bad_lastmod = [entry['loc'] for entry in entries
                   if entry.get('lastmod') and not _is_date_like(entry['lastmod'])]
    if bad_lastmod:
        findings.append(_finding(
            'low', 'sitemap_lastmod_not_iso8601', {'values': _samples(bad_lastmod)},
            'Use W3C/ISO 8601 dates for <lastmod>; unparseable values are ignored.'))
    bad_freq = sorted({entry['changefreq'] for entry in entries
                       if entry.get('changefreq')
                       and entry['changefreq'].lower() not in _SITEMAP_CHANGEFREQ})
    if bad_freq:
        findings.append(_finding(
            'low', 'sitemap_changefreq_invalid',
            {'values': _samples(bad_freq), 'allowed': list(_SITEMAP_CHANGEFREQ)},
            'Use one of the protocol changefreq values, or omit the element.'))
    bad_priority = []
    for entry in entries:
        raw = entry.get('priority')
        if not raw:
            continue
        try:
            number = float(raw)
        except ValueError:
            bad_priority.append(raw)
            continue
        if not 0.0 <= number <= 1.0:
            bad_priority.append(raw)
    if bad_priority:
        findings.append(_finding(
            'low', 'sitemap_priority_invalid', {'values': _samples(sorted(set(bad_priority)))},
            'Priority must be a number between 0.0 and 1.0, or omitted. Note that '
            'major crawlers state they ignore it.'))
    return findings


# --------------------------------------------------------------------------
# robots.txt. Crawler classification distinguishes search indexing from AI
# assistant retrieval and AI training. These are different consequences and
# must never be reported as the same problem.
# --------------------------------------------------------------------------

# Static local list, last reviewed 2026-10-24. Tokens and their purposes are set
# by each operator and change without notice: verify against the operator's own
# published documentation before acting. Absence from this list is not a verdict.
CRAWLER_CLASSES = {
    'googlebot': 'search_index',
    'googlebot-news': 'search_index',
    'googlebot-image': 'search_index',
    'bingbot': 'search_index',
    'applebot': 'search_index',
    'duckduckbot': 'search_index',
    'yandexbot': 'search_index',
    'baiduspider': 'search_index',
    'oai-searchbot': 'ai_assistant_retrieval',
    'chatgpt-user': 'ai_assistant_retrieval',
    'perplexitybot': 'ai_assistant_retrieval',
    'claude-searchbot': 'ai_assistant_retrieval',
    'claude-user': 'ai_assistant_retrieval',
    'gptbot': 'ai_training',
    'google-extended': 'gemini_training_grounding',
    'applebot-extended': 'ai_training',
    'ccbot': 'ai_training',
    'claudebot': 'ai_training',
    'bytespider': 'ai_training',
    'meta-externalagent': 'ai_training',
}

CRAWLER_CLASS_MEANING = {
    'search_index': 'Crawls for a search index. Blocking it can affect inclusion in that provider search index.',
    'ai_assistant_retrieval': 'Builds an assistant search index or fetches pages for user answers. Rules can affect that assistant; they do not control Google/Bing indexing. User-triggered fetchers may apply robots rules differently.',
    'ai_training': 'Collects data for model training. Blocking the training token alone does not affect separate search/retrieval tokens; review the operator policy.',
    'gemini_training_grounding': 'Google-Extended controls Gemini training and specified Gemini/Vertex grounding. It does not control Google Search ranking, AI Overviews or AI Mode; those use Googlebot controls.',
}


def parse_robots_txt(text):
    """Parse robots.txt into user-agent groups. Returns (groups, sitemaps, notes)."""
    notes = {}
    if not isinstance(text, str):
        text = ''
    if len(text) > MAX_ROBOTS_CHARS:
        notes['body_truncated'] = True
        text = text[:MAX_ROBOTS_CHARS]
    lines = text.splitlines()
    if len(lines) > MAX_ROBOTS_LINES:
        notes['lines_truncated'] = True
        lines = lines[:MAX_ROBOTS_LINES]
    groups = []
    sitemaps = []
    current = None
    for line in lines:
        line = line.split('#', 1)[0].strip()
        if not line or ':' not in line:
            continue
        field, _, value = line.partition(':')
        field = field.strip().lower()
        value = _clean(value, MAX_URL_CHARS)
        if field == 'user-agent':
            agent = value.lower()
            if current is not None and current['started_rules']:
                current = None
            if current is None:
                current = {'agents': [], 'allow': [], 'disallow': [], 'started_rules': False}
                groups.append(current)
            current['agents'].append(agent)
        elif field in ('allow', 'disallow'):
            if current is None:
                current = {'agents': ['*'], 'allow': [], 'disallow': [],
                           'started_rules': False}
                groups.append(current)
                notes['rules_before_user_agent'] = True
            current['started_rules'] = True
            current[field].append(value)
        elif field == 'sitemap' and value:
            sitemaps.append(value)
    for group in groups:
        group.pop('started_rules', None)
    return groups, sitemaps, notes


def robots_group_for(groups, agent):
    """Most specific matching group for one agent token, with '*' as fallback."""
    agent = agent.lower()
    best = None
    fallback = None
    for group in groups:
        for candidate in group['agents']:
            if candidate == '*':
                fallback = fallback or group
            elif candidate and (agent == candidate or agent.startswith(candidate)):
                if best is None or len(candidate) > best[0]:
                    best = (len(candidate), group)
    return best[1] if best else fallback


def robots_blocks_root(group):
    """True when the group disallows the site root for every path."""
    if not group:
        return False
    if any(rule == '/' for rule in group['allow']):
        return False
    return any(rule == '/' for rule in group['disallow'])


def classify_robots_blocking(groups):
    """Report which crawler class each blocked agent belongs to, without conflating them."""
    blocked = {name:[] for name in CRAWLER_CLASS_MEANING}
    for agent in sorted(CRAWLER_CLASSES):
        group = robots_group_for(groups, agent)
        if robots_blocks_root(group):
            blocked[CRAWLER_CLASSES[agent]].append(agent)
    return blocked


# --------------------------------------------------------------------------
# Local business NAP extraction.
# --------------------------------------------------------------------------

_NAP_TYPES = ('LocalBusiness', 'Organization', 'Store', 'Restaurant', 'Dentist',
              'MedicalBusiness', 'ProfessionalService', 'FoodEstablishment', 'Place')


def _digits(value):
    return re.sub(r'\D', '', value or '')


def extract_nap(facts, nodes):
    """Extract name/address/phone candidates from visible text and JSON-LD."""
    names = []
    phones = []
    addresses = []
    hours = []
    geo = []
    for node in nodes:
        types = _jsonld_types(node)
        if not any(name in types for name in _NAP_TYPES):
            continue
        label = _jsonld_name(node.get('name'))
        if label:
            names.append({'source': 'jsonld', 'value': label, 'types': types})
        phone = node.get('telephone')
        if isinstance(phone, str) and _digits(phone):
            phones.append({'source': 'jsonld', 'value': _clean(phone, 64)})
        address = node.get('address')
        rendered = _render_address(address)
        if rendered:
            addresses.append({'source': 'jsonld', 'value': rendered})
        opening = node.get('openingHours') or node.get('openingHoursSpecification')
        if opening:
            hours.append({'source': 'jsonld', 'present': True,
                          'value_type': type(opening).__name__})
        point = node.get('geo')
        if isinstance(point, dict):
            latitude, longitude = point.get('latitude'), point.get('longitude')
            if latitude is not None and longitude is not None:
                geo.append({'source': 'jsonld',
                            'latitude': _clean(str(latitude), 32),
                            'longitude': _clean(str(longitude), 32)})
    titles = [item for item in facts.titles if item]
    if titles:
        names.append({'source': 'title', 'value': _clean(titles[0], 120), 'types': []})
    for value in facts.meta_values('og:site_name'):
        if value:
            names.append({'source': 'meta[og:site_name]', 'value': _clean(value, 120),
                          'types': []})
    text = facts.visible_text
    for match in _PHONE.finditer(text[:MAX_TEXT_CHARS]):
        digits = _digits(match.group(0))
        if 8 <= len(digits) <= 15:
            phones.append({'source': 'visible_text', 'value': _clean(match.group(0), 64),
                           'heuristic': True})
        if len(phones) >= MAX_SAMPLES * 3:
            break
    for line in _address_candidates(text):
        addresses.append({'source': 'visible_text', 'value': line, 'heuristic': True})
    variants = sorted({_digits(item['value']) for item in phones if _digits(item['value'])})
    return {'name_candidates': _samples(names),
            'phone_candidates': _samples(phones),
            'phone_normalised_variants': _samples(variants),
            'address_candidates': _samples(addresses),
            'opening_hours_signals': _samples(hours),
            'geo_coordinates': _samples(geo),
            'extraction': 'JSON-LD values are read as published; visible-text phone and '
                          'address matches are regex heuristics and may include false '
                          'positives or miss valid formats.',
            'verification': 'Google Business Profile ownership, listing status, review '
                            'counts and local ranking are NOT checked and cannot be '
                            'determined from page markup.'}


def _render_address(address):
    if isinstance(address, str):
        return _clean(address, 200)
    if isinstance(address, dict):
        parts = []
        for key in ('streetAddress', 'addressLocality', 'addressRegion',
                    'postalCode', 'addressCountry'):
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(_clean(value, 80))
            elif isinstance(value, dict):
                label = _jsonld_name(value)
                if label:
                    parts.append(label)
        return _clean(', '.join(parts), 200)
    if isinstance(address, list):
        for item in address:
            rendered = _render_address(item)
            if rendered:
                return rendered
    return ''


def _address_candidates(text):
    found = []
    for chunk in re.split(r'[|•\n\r]+|(?<=[a-z0-9])\.\s', text[:MAX_TEXT_CHARS]):
        chunk = chunk.strip()
        if 12 <= len(chunk) <= 200 and _STREET_HINT.search(chunk) and _POSTCODE.search(chunk):
            found.append(_clean(chunk, 200))
        if len(found) >= MAX_SAMPLES:
            break
    return found


def check_nap(evidence):
    findings = []
    if not evidence['name_candidates']['total']:
        findings.append(_finding(
            'medium', 'business_name_not_found', {'name_candidates': 0},
            'State the business name in visible text and in LocalBusiness markup.'))
    if not evidence['phone_candidates']['total']:
        findings.append(_finding(
            'medium', 'phone_not_found', {'phone_candidates': 0},
            'Publish a phone number in visible text and as the telephone property.'))
    if not evidence['address_candidates']['total']:
        findings.append(_finding(
            'medium', 'address_not_found', {'address_candidates': 0},
            'If a public business address is appropriate, publish it consistently; service-area businesses should not expose a private home address solely for this check.'))
    if not any(item['source'] == 'jsonld' for item in evidence['name_candidates']['sample']):
        findings.append(_finding(
            'low', 'localbusiness_jsonld_absent', {'checked_types': list(_NAP_TYPES)},
            'Add LocalBusiness (or a more specific subtype) JSON-LD with name, address '
            'and telephone.'))
    variants = evidence['phone_normalised_variants']
    if variants['total'] > 1:
        findings.append(_finding(
            'low', 'phone_variants_inconsistent', {'variants': variants},
            'Multiple distinct phone numbers were detected. Confirm the page and your '
            'listings use one consistent primary number.'))
    if not evidence['opening_hours_signals']['total']:
        findings.append(_finding(
            'info', 'opening_hours_markup_absent', {'opening_hours_signals': 0},
            'Add openingHours markup if the location has public hours.'))
    return findings


# --------------------------------------------------------------------------
# GEO: structural heuristics for machine-readable, citable content. These
# describe page structure only. Whether any assistant cites a page is decided
# by that assistant; this module promises no citation and no ranking outcome.
# --------------------------------------------------------------------------

GEO_MIN_HEADINGS = 3
GEO_MIN_LIST_ITEMS = 3


def geo_structure_evidence(facts, nodes, metrics, authorship):
    outbound = []
    for anchor in facts.anchors:
        href = anchor['href']
        if not href or href.startswith('#'):
            continue
        parts = urlsplit(href)
        if parts.scheme.lower() in ('http', 'https') and parts.netloc:
            outbound.append({'href': href, 'rel': anchor['rel'],
                             'text': anchor['text']})
    definition_lead = bool(re.match(
        r'^\s*\S[^.!?]{10,300}?\s(?:is|are|means|refers to|adalah|merupakan)\s',
        metrics['visible_text_excerpt'], re.IGNORECASE))
    return {'headings': metrics['heading_outline']['total'],
            'heading_levels': sorted({item['level'] for item
                                      in metrics['heading_outline']['sample']}),
            'list_items': metrics['list_items'],
            'tables': metrics['tables'],
            'word_count': metrics['word_count'],
            'outbound_citations': _samples(outbound),
            'author_signals': authorship['author_signals']['total'],
            'date_signals': authorship['date_signals']['total'],
            'structured_data_nodes': len(nodes),
            'opens_with_definition_sentence': definition_lead,
            'interpretation': 'Structural observations about machine readability. Not a '
                              'prediction, guarantee or probability of being cited, '
                              'retrieved, summarised or ranked by any AI assistant or '
                              'search engine.'}


def check_geo_structure(evidence):
    findings = []
    if evidence['headings'] < GEO_MIN_HEADINGS:
        findings.append(_finding(
            'low', 'geo_heading_structure_shallow',
            {'headings': evidence['headings'], 'threshold': GEO_MIN_HEADINGS},
            'Break the content into clearly titled sections so a specific passage can '
            'be located and quoted in context.'))
    if evidence['list_items'] < GEO_MIN_LIST_ITEMS and not evidence['tables']:
        findings.append(_finding(
            'info', 'geo_enumerated_content_absent',
            {'list_items': evidence['list_items'], 'tables': evidence['tables']},
            'Where content is genuinely enumerable (steps, specifications, '
            'comparisons), lists or tables are easier to extract accurately.'))
    if not evidence['date_signals']:
        findings.append(_finding(
            'low', 'geo_date_signals_absent', {'date_signals': 0},
            'Publish explicit published/modified dates so recency is machine-readable.'))
    if not evidence['author_signals']:
        findings.append(_finding(
            'info', 'geo_author_signals_absent', {'author_signals': 0},
            'Attribute the content to an identifiable author or organisation where '
            'that is meaningful for the subject.'))
    if not evidence['outbound_citations']['total']:
        findings.append(_finding(
            'info', 'geo_outbound_citations_absent', {'outbound_citations': 0},
            'Link the primary sources for factual claims so they can be verified.'))
    if not evidence['structured_data_nodes']:
        findings.append(_finding(
            'low', 'geo_structured_data_absent', {'structured_data_nodes': 0},
            'Add JSON-LD describing the page entity so its facts are unambiguous.'))
    if not evidence['opens_with_definition_sentence']:
        findings.append(_finding(
            'info', 'geo_lead_not_self_contained',
            {'excerpt_checked': True, 'note': 'regex heuristic over the opening excerpt'},
            'Consider opening with a self-contained sentence that answers the page\'s '
            'core question without requiring surrounding context.'))
    return findings


def check_geo_robots(blocked, robots_available):
    """Report blocking per crawler class; never merge the classes."""
    findings = []
    if not robots_available:
        return findings
    if blocked['search_index']:
        findings.append(_finding(
            'high', 'robots_blocks_search_crawler',
            {'agents': blocked['search_index'],
             'consequence': CRAWLER_CLASS_MEANING['search_index']},
            'robots.txt disallows a search engine crawler at the site root. Remove '
            'that rule if the site should be indexed.'))
    if blocked['ai_assistant_retrieval']:
        findings.append(_finding(
            'info', 'robots_blocks_ai_assistant_crawler',
            {'agents': blocked['ai_assistant_retrieval'],
             'consequence': CRAWLER_CLASS_MEANING['ai_assistant_retrieval'],
             'does_not_affect': 'Google/Bing search engine indexing'},
            'This is reported as a configuration fact, not a defect. Keep it if '
            'excluding the site from assistant answers is intended.'))
    if blocked['ai_training']:
        findings.append(_finding(
            'info', 'robots_blocks_ai_training_crawler',
            {'agents': blocked['ai_training'],
             'consequence': CRAWLER_CLASS_MEANING['ai_training'],
             'does_not_affect': 'search engine indexing or assistant retrieval'},
            'This is reported as a configuration fact, not a defect. A training-crawler '
            'block is a content-licensing decision and must not be "fixed" to improve '
            'search visibility, because it does not affect it.'))
    if blocked.get('gemini_training_grounding'):
        findings.append(_finding('info','robots_google_extended_policy',
            {'agents':blocked['gemini_training_grounding'],
             'consequence':CRAWLER_CLASS_MEANING['gemini_training_grounding'],
             'does_not_affect':'Google Search indexing/ranking and Google Search AI features'},
            'Keep or change this policy based on intended Gemini training/grounding permissions; do not treat it as a Google Search indexing fix.'))
    return findings


# --------------------------------------------------------------------------
# Keyword clustering. Local string analysis only. No search volume, no
# difficulty, no competition and no ranking data exists in these inputs.
# --------------------------------------------------------------------------

_STOPWORDS = frozenset((
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'for', 'from', 'how',
    'in', 'is', 'it', 'of', 'on', 'or', 'that', 'the', 'this', 'to', 'vs', 'was',
    'what', 'when', 'where', 'which', 'who', 'why', 'with',
    'dan', 'di', 'ke', 'dari', 'untuk', 'yang', 'adalah', 'atau', 'pada', 'dengan',
    'apa', 'bagaimana', 'kenapa', 'itu', 'ini',
))


def _normalize_keyword(value):
    folded = unicodedata.normalize('NFKD', value).casefold()
    return _SPACES.sub(' ', re.sub(r'[^\w\s]', ' ', folded)).strip()


def _keyword_tokens(value):
    tokens = [token for token in _normalize_keyword(value).split() if token]
    content = [token for token in tokens if token not in _STOPWORDS]
    return tuple(sorted(set(content or tokens)))


def _normalize_serp_url(value):
    parts = urlsplit(value)
    host = (parts.hostname or '').lower()
    host = host[4:] if host.startswith('www.') else host
    path = parts.path.rstrip('/') or '/'
    return host + path


def _jaccard(left, right):
    if not left or not right:
        return 0.0
    left_set, right_set = set(left), set(right)
    union = len(left_set | right_set)
    return len(left_set & right_set) / union if union else 0.0


def _group(keys, similarity, threshold):
    """Deterministic single-link grouping over a sorted key list (union-find)."""
    parent = {key: key for key in keys}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    pairs = []
    for index, left in enumerate(keys):
        for right in keys[index + 1:]:
            score = similarity(left, right)
            if score >= threshold:
                pairs.append((left, right, score))
                root_left, root_right = find(left), find(right)
                if root_left != root_right:
                    parent[max(root_left, root_right)] = min(root_left, root_right)
    clusters = {}
    for key in keys:
        clusters.setdefault(find(key), []).append(key)
    return [sorted(members) for _, members in sorted(clusters.items())], pairs


def _validate_keywords(payload):
    keywords = payload.get('keywords')
    if not isinstance(keywords, list) or not keywords:
        _fail('invalid_keywords', 'keywords must be a non-empty array of strings')
    if len(keywords) > MAX_KEYWORDS:
        _fail('too_many_keywords', 'keywords accepts at most %d entries' % MAX_KEYWORDS)
    cleaned = []
    for item in keywords:
        if not isinstance(item, str) or not item.strip():
            _fail('invalid_keywords', 'every keyword must be a non-empty string')
        if len(item) > MAX_KEYWORD_CHARS:
            _fail('invalid_keywords', 'a keyword exceeds the maximum accepted length')
        cleaned.append(_clean(item, MAX_KEYWORD_CHARS))
    return cleaned


def _validate_serp(payload, keywords):
    serp = payload.get('serp_results')
    if serp is None:
        return None
    if not isinstance(serp, dict):
        _fail('invalid_serp_results', 'serp_results must be an object keyed by keyword')
    if len(serp) > MAX_KEYWORDS:
        _fail('invalid_serp_results',
              'serp_results accepts at most %d keywords' % MAX_KEYWORDS)
    known = {_clean(item, MAX_KEYWORD_CHARS) for item in keywords}
    table = {}
    for key in sorted(key for key in serp if isinstance(key, str)):
        urls = serp[key]
        if not isinstance(urls, list):
            _fail('invalid_serp_results', 'each serp_results value must be an array of URLs')
        if len(urls) > MAX_SERP_URLS:
            _fail('invalid_serp_results',
                  'each keyword accepts at most %d result URLs' % MAX_SERP_URLS)
        cleaned = []
        for item in urls:
            if not isinstance(item, str) or not item.strip():
                _fail('invalid_serp_results', 'each result URL must be a non-empty string')
            if len(item) > MAX_URL_CHARS:
                _fail('invalid_serp_results', 'a result URL exceeds the maximum length')
            cleaned.append(_clean(item, MAX_URL_CHARS))
        key = _clean(key, MAX_KEYWORD_CHARS)
        if key not in known:
            _fail('invalid_serp_results',
                  'serp_results contains a keyword that is not in the keywords list')
        table[key] = cleaned
    return table


# --------------------------------------------------------------------------
# Shared operation plumbing.
# --------------------------------------------------------------------------

_UNTRUSTED_NOTE = ('All page, sitemap and robots.txt content is treated as untrusted '
                   'data: values are sanitised and truncated for evidence and are never '
                   'executed or followed as instructions.')
_NO_METRICS_NOTE = ('No ranking position, search volume, traffic, click, impression, '
                    'keyword difficulty, domain authority or composite score is produced; '
                    'none of those are derivable from the supplied inputs.')


def _method(operation, state, parsers, extra=None):
    method = {'analysis': 'deterministic_static_rules',
              'operation': operation,
              'fetches_used': state['fetches'],
              'fetch_budget': state['budget'],
              'fetched': _samples(state['fetched']),
              'parsers': list(parsers),
              'remote_content': 'untrusted',
              'deterministic': True,
              'uses_clock': False,
              'uses_model_inference': False}
    if extra:
        method.update(extra)
    return method


def _page_context(payload, fetcher, operation, budget):
    """Validate a {url} payload, fetch the page and parse it once."""
    _require_payload(payload, ('url',), ('url',))
    url = _require_url(payload['url'])
    _require_fetcher(fetcher)
    state = _new_state(budget)
    response = _fetch(fetcher, url, state)
    if response is None:
        return url, state, None, None, None, None
    facts = parse_html(response['text'])
    response = dict(response, parse_error=facts.parse_error)
    nodes, documents, jsonld_findings = parse_jsonld(facts.jsonld_blocks)
    metrics = content_metrics(facts)
    return url, state, response, facts, (nodes, documents, jsonld_findings), metrics


def _unavailable(operation, url, state, parsers, limitations):
    """Uniform degraded result when the page itself could not be retrieved."""
    return _result(
        operation, 'degraded', _method(operation, state, parsers),
        {'url': _clean(url), 'page_retrieved': False},
        _fetch_errors_to_findings(state),
        ['The page could not be retrieved, so no page checks ran. Nothing about the '
         'page content is asserted.', _UNTRUSTED_NOTE, _NO_METRICS_NOTE] + list(limitations))


def _status(findings_degraded, state):
    return 'degraded' if (findings_degraded or state['errors']) else 'completed'


# --------------------------------------------------------------------------
# Operations.
# --------------------------------------------------------------------------

def page_audit(payload, fetcher):
    operation = 'seo.page_audit'
    url, state, response, facts, jsonld, metrics = _page_context(payload, fetcher, operation, 1)
    if response is None:
        return _unavailable(operation, url, state, ('html.parser',), [])
    nodes, _documents, jsonld_findings = jsonld
    index_findings, index_evidence = check_indexability(facts, response)
    findings = (check_response(response, url) + check_title(facts)
                + check_meta_description(facts) + check_headings(facts)
                + check_canonical(facts, url) + index_findings
                + check_images(facts) + check_document_shell(facts)
                + check_content(metrics) + jsonld_findings
                + _fetch_errors_to_findings(state))
    titles = [item for item in facts.titles if item]
    descriptions = [item for item in facts.meta_values('description') if item]
    evidence = {
        'url': _clean(url), 'final_url': _clean(response['url']),
        'page_retrieved': True, 'status': response['status'],
        'content_type': response['content_type'],
        'title': titles[0] if titles else None,
        'title_tags': len(facts.titles),
        'meta_description': _clean(descriptions[0], 320) if descriptions else None,
        'meta_description_tags': facts.meta_count('description'),
        'canonical': _samples([item['href'] for item in facts.links_by_rel('canonical')]),
        'indexability': index_evidence,
        'html_lang': facts.lang,
        'images': {'total': len(facts.images),
                   'missing_alt': sum(1 for item in facts.images if not item['has_alt']),
                   'empty_alt': sum(1 for item in facts.images
                                    if item['has_alt'] and not item['alt'])},
        'content': metrics,
        'structured_data_nodes': len(nodes),
        'extraction_truncated': dict(facts.truncated),
    }
    return _result(operation, _status(False, state),
                   _method(operation, state, ('html.parser', 'json')),
                   evidence, findings,
                   ['Checks cover the delivered HTML only. Client-rendered content, '
                    'redirect chains, rendering, Core Web Vitals and crawl behaviour are '
                    'not evaluated here.',
                    'Indexability is read from this page\'s meta robots and X-Robots-Tag '
                    'only; robots.txt is checked by seo.technical.',
                    _UNTRUSTED_NOTE, _NO_METRICS_NOTE])


def technical(payload, fetcher):
    operation = 'seo.technical'
    _require_payload(payload, ('url',), ('url',))
    url = _require_url(payload['url'])
    _require_fetcher(fetcher)
    state = _new_state(MAX_TECHNICAL_FETCHES)
    findings = []
    origin = urlsplit(url)
    base = origin.scheme + '://' + origin.netloc

    page = _fetch(fetcher, url, state)
    page_evidence = {'retrieved': False}
    if page is not None:
        facts = parse_html(page['text'])
        page = dict(page, parse_error=facts.parse_error)
        index_findings, index_evidence = check_indexability(facts, page)
        findings.extend(check_response(page, url) + index_findings)
        page_evidence = {'retrieved': True, 'status': page['status'],
                         'content_type': page['content_type'],
                         'indexability': index_evidence,
                         'canonical': _samples([item['href'] for item
                                                in facts.links_by_rel('canonical')]),
                         'security_headers_present': sorted(
                             key for key in ('strict-transport-security', 'x-robots-tag',
                                             'content-encoding', 'vary', 'link')
                             if key in page['headers'])}

    robots_url = base + '/robots.txt'
    robots = _fetch(fetcher, robots_url, state)
    robots_evidence = {'url': _clean(robots_url), 'retrieved': False}
    declared_sitemaps = []
    if robots is None:
        _consume_error(state, _clean(robots_url))
        findings.append(_finding(
            'medium', 'robots_txt_unavailable', {'url': _clean(robots_url)},
            'Publish a reachable /robots.txt. A missing file is treated as "crawling '
            'allowed", but an error response can be treated as disallow by some crawlers.'))
    else:
        groups, declared_sitemaps, notes = parse_robots_txt(robots['text'])
        blocked = classify_robots_blocking(groups)
        robots_evidence = {'url': _clean(robots_url), 'retrieved': True,
                           'status': robots['status'],
                           'content_type': robots['content_type'],
                           'groups': len(groups),
                           'declared_sitemaps': _samples(declared_sitemaps),
                           'root_disallowed_by_class': blocked,
                           'class_meaning': CRAWLER_CLASS_MEANING,
                           'crawler_list': 'static local list reviewed 2026-10-24; '
                                           'verify tokens against operator documentation',
                           'parse_notes': notes}
        if robots['status'] != 200:
            findings.append(_finding(
                'medium', 'robots_txt_status_not_ok',
                {'status': robots['status'], 'url': _clean(robots_url)},
                'Serve /robots.txt with a 200 response.'))
        kind = robots['content_type'].split(';')[0].strip()
        if kind and kind != 'text/plain':
            findings.append(_finding(
                'low', 'robots_txt_not_plain_text', {'content_type': kind},
                'Serve /robots.txt as text/plain.'))
        findings.extend(check_geo_robots(blocked, True))
        if not declared_sitemaps:
            findings.append(_finding(
                'low', 'robots_txt_sitemap_directive_absent', {'declared_sitemaps': 0},
                'Declare the sitemap location with a Sitemap: directive in robots.txt.'))
        if notes.get('rules_before_user_agent'):
            findings.append(_finding(
                'low', 'robots_txt_rule_before_user_agent', {'note': True},
                'Place Allow/Disallow rules after a User-agent line; leading rules are '
                'ambiguous and handled inconsistently.'))

    candidates = []
    for value in declared_sitemaps:
        if _same_site(value, url) and value not in candidates:
            candidates.append(value)
        elif not _same_site(value, url):
            findings.append(_finding(
                'low', 'robots_txt_sitemap_other_host',
                {'value': value, 'robots_host': _host(url),
                 'note': 'not fetched; this analyser never follows other hosts'},
                'Confirm cross-host sitemap submission is configured, or declare a '
                'same-host sitemap.'))
    for default in (base + '/sitemap.xml', base + '/sitemap_index.xml'):
        if default not in candidates:
            candidates.append(default)

    sitemaps = []
    for candidate in candidates:
        if state['fetches'] >= state['budget']:
            findings.append(_finding(
                'info', 'sitemap_candidates_not_checked',
                {'remaining': _samples(candidates[len(sitemaps):]),
                 'fetch_budget': state['budget']},
                'The fetch budget was reached. Run seo.sitemap on the remaining '
                'candidates to check them.'))
            break
        response = _fetch(fetcher, candidate, state)
        if response is None:
            sitemaps.append({'url': _clean(candidate), 'retrieved': False})
            continue
        kind, entries, parse_findings = parse_sitemap_xml(response['text'])
        reachable = response['status'] == 200 and kind in ('urlset', 'sitemapindex')
        sitemaps.append({'url': _clean(candidate), 'retrieved': True,
                         'status': response['status'],
                         'content_type': response['content_type'],
                         'root_element': kind, 'entries_analysed': len(entries),
                         'parses_as_sitemap': reachable})
        if response['status'] == 200:
            findings.extend(parse_findings)
        if reachable:
            break

    if not any(item.get('parses_as_sitemap') for item in sitemaps):
        findings.append(_finding(
            'medium', 'sitemap_not_confirmed',
            {'candidates_checked': _samples([item['url'] for item in sitemaps])},
            'No checked candidate returned a parseable sitemap. Publish one and declare '
            'it in robots.txt. Other locations may still exist and were not checked.'))

    findings.extend(_fetch_errors_to_findings(state))
    evidence = {'url': _clean(url), 'origin': _clean(base), 'page': page_evidence,
                'robots_txt': robots_evidence,
                'sitemap_candidates': _samples(sitemaps)}
    degraded = page is None or robots is None
    return _result(operation, _status(degraded, state),
                   _method(operation, state, ('html.parser', 'robots.txt', 'ElementTree')),
                   evidence, findings,
                   ['At most %d fetches run: the page, /robots.txt and up to two sitemap '
                    'candidates. Each failure degrades only its own checks.'
                    % MAX_TECHNICAL_FETCHES,
                    'Crawl budget, log analysis, redirect chains, server performance, '
                    'rendering and HTTP/2 behaviour are out of scope.',
                    'Crawler classification uses a static local token list; it is not a '
                    'live registry and operators change tokens without notice.',
                    _UNTRUSTED_NOTE, _NO_METRICS_NOTE])


def content(payload, fetcher):
    operation = 'seo.content'
    url, state, response, facts, jsonld, metrics = _page_context(payload, fetcher, operation, 1)
    if response is None:
        return _unavailable(operation, url, state, ('html.parser',), [])
    nodes, _documents, _jsonld_findings = jsonld
    authorship = authorship_evidence(facts, nodes)
    duplicate_evidence = {
        'title_tags': len(facts.titles),
        'meta_description_tags': facts.meta_count('description'),
        'canonical_tags': len(facts.links_by_rel('canonical')),
        'h1_count': sum(1 for item in facts.headings if item['level'] == 1),
        'duplicate_meta_keys': _samples(sorted(
            key for key in {item['key'] for item in facts.metas if item['key']}
            if facts.meta_count(key) > 1)),
    }
    findings = (check_response(response, url) + check_title(facts)
                + check_meta_description(facts) + check_headings(facts)
                + check_content(metrics) + check_authorship(authorship)
                + _fetch_errors_to_findings(state))
    evidence = {'url': _clean(url), 'page_retrieved': True, 'status': response['status'],
                'metrics': metrics, 'duplicate_markup': duplicate_evidence,
                'authorship': authorship}
    return _result(operation, _status(False, state),
                   _method(operation, state, ('html.parser', 'json')),
                   evidence, findings,
                   ['Word counts and heading outlines describe the delivered HTML; '
                    'client-rendered text is not included.',
                    'Authorship and date items are observed markup labels. This '
                    'operation does not compute an E-E-A-T score and makes no judgement '
                    'about expertise, accuracy or trustworthiness.',
                    'Duplicate detection covers repeated tags within this one document. '
                    'Cross-page duplicate or near-duplicate content is not evaluated.',
                    'Readability grades, sentiment, topical coverage and semantic '
                    'relevance scores are not produced.',
                    _UNTRUSTED_NOTE, _NO_METRICS_NOTE])


def schema(payload, fetcher):
    operation = 'seo.schema'
    url, state, response, facts, jsonld, _metrics = _page_context(payload, fetcher, operation, 1)
    if response is None:
        return _unavailable(operation, url, state, ('json',), [])
    nodes, documents, jsonld_findings = jsonld
    property_findings, node_evidence = check_jsonld_properties(nodes)
    findings = check_response(response, url) + jsonld_findings + property_findings
    if not facts.jsonld_blocks:
        findings.append(_finding(
            'info', 'jsonld_absent', {'jsonld_scripts': 0},
            'Add JSON-LD structured data if this page represents an entity that search '
            'engines or assistants should understand precisely.'))
    if facts.counts['other_ld_script']:
        findings.append(_finding(
            'low', 'jsonld_script_type_incorrect',
            {'scripts': facts.counts['other_ld_script']},
            'Use type="application/ld+json"; other type values are not recognised.'))
    if facts.itemtypes:
        findings.append(_finding(
            'info', 'microdata_present_not_parsed',
            {'itemtypes': _samples(sorted(set(facts.itemtypes)))},
            'Microdata/RDFa attributes were detected but are not parsed by this '
            'operation, which reads JSON-LD only.'))
    findings.extend(_fetch_errors_to_findings(state))
    evidence = {'url': _clean(url), 'page_retrieved': True,
                'jsonld_scripts': facts.counts['jsonld_script'],
                'jsonld_blocks_parsed': len(documents),
                'jsonld_nodes': node_evidence,
                'microdata_itemtypes': _samples(sorted(set(facts.itemtypes))),
                'required_property_table': {key: list(value) for key, value
                                            in sorted(REQUIRED_PROPERTIES.items())},
                'extraction_truncated': dict(facts.truncated)}
    return _result(operation, _status(False, state),
                   _method(operation, state, ('html.parser', 'json'),
                           {'validation_scope': 'json_syntax_plus_selected_required_properties'}),
                   evidence, findings,
                   ['This is JSON-LD syntax parsing plus a presence check for the '
                    'selected properties listed in required_property_table. It is NOT '
                    'full schema.org validation and NOT Google rich-result or '
                    'eligibility validation.',
                    'Property value types, vocabularies, enumerations, nested-entity '
                    'correctness, image/logo requirements and policy requirements are '
                    'not checked. Types absent from the table are reported but not '
                    'property-checked.',
                    'Confirm rich-result eligibility with the search engine\'s own '
                    'structured-data testing tool before relying on it.',
                    'Microdata and RDFa are detected only, never parsed.',
                    _UNTRUSTED_NOTE, _NO_METRICS_NOTE])


def sitemap(payload, fetcher):
    operation = 'seo.sitemap'
    _require_payload(payload, ('url',), ('url',))
    url = _require_url(payload['url'])
    _require_fetcher(fetcher)
    state = _new_state(MAX_SITEMAP_FETCHES)
    findings = []
    documents = []
    queue = [url]
    visited = []
    skipped_other_host = []
    analysed_urls = 0

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        if not _same_site(current, url):
            skipped_other_host.append(current)
            continue
        if state['fetches'] >= state['budget']:
            findings.append(_finding(
                'info', 'sitemap_children_not_fetched',
                {'remaining': _samples([current] + queue), 'fetch_budget': state['budget']},
                'Child sitemaps beyond the fetch budget were listed but not fetched. '
                'Run seo.sitemap on them directly.'))
            break
        visited.append(current)
        response = _fetch(fetcher, current, state)
        if response is None:
            documents.append({'url': _clean(current), 'retrieved': False})
            continue
        if response['status'] != 200:
            findings.append(_finding(
                'high', 'sitemap_status_not_ok',
                {'url': _clean(current), 'status': response['status']},
                'Serve the sitemap with a 200 response.'))
        kind = response['content_type'].split(';')[0].strip()
        if kind and kind not in ('application/xml', 'text/xml', 'application/gzip',
                                 'application/x-gzip'):
            findings.append(_finding(
                'low', 'sitemap_content_type_unexpected',
                {'url': _clean(current), 'content_type': kind},
                'Serve XML sitemaps as application/xml or text/xml.'))
        root_kind, entries, parse_findings = parse_sitemap_xml(response['text'])
        findings.extend(parse_findings)
        locs = [entry.get('loc', '') for entry in entries]
        findings.extend(check_sitemap_entries(entries, current))
        analysed_urls += len(entries)
        documents.append({'url': _clean(current), 'retrieved': True,
                          'status': response['status'],
                          'content_type': response['content_type'],
                          'root_element': root_kind,
                          'entries_analysed': len(entries),
                          'entries': _samples(entries),
                          'locs': _samples(locs)})
        if root_kind == 'sitemapindex':
            for value in locs:
                if not value:
                    continue
                if not _same_site(value, url):
                    skipped_other_host.append(value)
                elif value not in visited and value not in queue:
                    queue.append(value)
        if analysed_urls >= MAX_SITEMAP_URLS:
            findings.append(_finding(
                'info', 'sitemap_url_analysis_cap_reached',
                {'analysed_urls': analysed_urls, 'cap': MAX_SITEMAP_URLS},
                'The analysis cap was reached; remaining entries were not inspected.'))
            break

    if skipped_other_host:
        findings.append(_finding(
            'low', 'sitemap_other_host_not_followed',
            {'values': _samples(sorted(set(skipped_other_host))),
             'sitemap_host': _host(url)},
            'Entries on other hosts are never fetched by this operation. Audit those '
            'hosts separately.'))
    findings.extend(_fetch_errors_to_findings(state))
    evidence = {'url': _clean(url), 'documents': _samples(documents),
                'documents_retrieved': sum(1 for item in documents if item.get('retrieved')),
                'urls_analysed': analysed_urls,
                'url_analysis_cap': MAX_SITEMAP_URLS,
                'other_host_entries_skipped': _samples(sorted(set(skipped_other_host)))}
    degraded = not any(item.get('retrieved') for item in documents)
    return _result(operation, _status(degraded, state),
                   _method(operation, state, ('ElementTree',),
                           {'xml_hardening': 'DOCTYPE/ENTITY/ATTLIST/NOTATION refused '
                                             'before parsing; no external entity or DTD '
                                             'resolution',
                            'recursion': 'same-host child sitemaps only, within the '
                                         'fetch budget'}),
                   evidence, findings,
                   ['At most %d sitemap documents are fetched and at most %d URLs are '
                    'analysed in total; samples and truncation are labelled in evidence.'
                    % (MAX_SITEMAP_FETCHES, MAX_SITEMAP_URLS),
                    'Recursion never leaves the sitemap host, so cross-host entries are '
                    'listed but never fetched.',
                    'Listed URLs are not fetched, so their status, indexability and '
                    'content are not verified by this operation.',
                    'Gzipped sitemaps are not decompressed; plain-text sitemap lists and '
                    'RSS/Atom feeds are not parsed.',
                    _UNTRUSTED_NOTE, _NO_METRICS_NOTE])


def local(payload, fetcher):
    operation = 'seo.local'
    url, state, response, facts, jsonld, metrics = _page_context(payload, fetcher, operation, 1)
    if response is None:
        return _unavailable(operation, url, state, ('html.parser',), [])
    nodes, _documents, jsonld_findings = jsonld
    nap = extract_nap(facts, nodes)
    findings = (check_response(response, url) + check_nap(nap) + jsonld_findings
                + _fetch_errors_to_findings(state))
    evidence = {'url': _clean(url), 'page_retrieved': True, 'nap': nap,
                'visible_text_chars': metrics['visible_text_chars'],
                'structured_data_nodes': len(nodes),
                'google_business_profile': {
                    'ownership_verified': False, 'listing_checked': False,
                    'local_ranking_checked': False, 'review_data_checked': False,
                    'reason': 'No Google Business Profile credential or API access is '
                              'configured, and none of this is observable in page markup.'}}
    return _result(operation, _status(False, state),
                   _method(operation, state, ('html.parser', 'json', 'regex')),
                   evidence, findings,
                   ['NAP values come from this page\'s visible text and JSON-LD. '
                    'Visible-text phone and address matches are regex heuristics that '
                    'can produce false positives and miss valid local formats.',
                    'Google Business Profile ownership, verification status, listing '
                    'accuracy, categories, reviews, photos and local pack ranking are '
                    'NOT verified and cannot be determined from page markup.',
                    'NAP consistency across external directories and citation sites is '
                    'not checked; only this page is read.',
                    'Service-area coverage, proximity effects and map placement are not '
                    'evaluated.',
                    _UNTRUSTED_NOTE, _NO_METRICS_NOTE])


def geo(payload, fetcher):
    operation = 'seo.geo'
    _require_payload(payload, ('url',), ('url',))
    url = _require_url(payload['url'])
    _require_fetcher(fetcher)
    state = _new_state(MAX_GEO_FETCHES)
    response = _fetch(fetcher, url, state)
    if response is None:
        return _unavailable(operation, url, state, ('html.parser',),
                            ['robots.txt was not checked because the page fetch failed.'])
    facts = parse_html(response['text'])
    response = dict(response, parse_error=facts.parse_error)
    nodes, _documents, jsonld_findings = parse_jsonld(facts.jsonld_blocks)
    metrics = content_metrics(facts)
    authorship = authorship_evidence(facts, nodes)
    structure = geo_structure_evidence(facts, nodes, metrics, authorship)
    findings = (check_response(response, url) + check_geo_structure(structure)
                + jsonld_findings)

    origin = urlsplit(url)
    robots_url = origin.scheme + '://' + origin.netloc + '/robots.txt'
    robots = _fetch(fetcher, robots_url, state)
    robots_evidence = {'url': _clean(robots_url), 'retrieved': False}
    if robots is None:
        _consume_error(state, _clean(robots_url))
        findings.append(_finding(
            'info', 'robots_txt_unavailable_for_crawler_review',
            {'url': _clean(robots_url)},
            'robots.txt could not be read, so crawler access was not reviewed. No '
            'assumption is made about which crawlers are allowed.'))
    else:
        groups, declared, notes = parse_robots_txt(robots['text'])
        blocked = classify_robots_blocking(groups)
        findings.extend(check_geo_robots(blocked, robots['status'] == 200))
        robots_evidence = {'url': _clean(robots_url), 'retrieved': True,
                           'status': robots['status'], 'groups': len(groups),
                           'declared_sitemaps': _samples(declared),
                           'root_disallowed_by_class': blocked,
                           'class_meaning': CRAWLER_CLASS_MEANING,
                           'crawler_list': 'static local list reviewed 2026-10-24; '
                                           'verify tokens against operator documentation',
                           'parse_notes': notes}
    findings.extend(_fetch_errors_to_findings(state))
    evidence = {'url': _clean(url), 'page_retrieved': True, 'structure': structure,
                'authorship': authorship, 'robots_txt': robots_evidence,
                'content': metrics}
    return _result(operation, _status(False, state),
                   _method(operation, state, ('html.parser', 'json', 'robots.txt'),
                           {'heuristics': 'structural_only'}),
                   evidence, findings,
                   ['These are structural heuristics about machine readability: heading '
                    'structure, enumerated content, explicit dates, attribution, '
                    'outbound sources and structured data.',
                    'No citation, inclusion, retrieval, summarisation or ranking outcome '
                    'in any AI assistant or search engine is promised, predicted or '
                    'scored. Those systems are opaque and change without notice.',
                    'Search-index crawlers, AI assistant retrieval crawlers and AI '
                    'training crawlers are reported as three separate classes. Blocking '
                    'a training crawler does not affect search indexing or assistant '
                    'answers and is never reported as a search visibility defect.',
                    'Crawler classification uses a static local token list, not a live '
                    'registry; verify each token against the operator\'s documentation.',
                    'Whether a page is actually used for model training cannot be '
                    'verified from robots.txt, which states a request, not a guarantee.',
                    _UNTRUSTED_NOTE, _NO_METRICS_NOTE])


def keyword_cluster(payload, fetcher=None):
    operation = 'seo.keyword_cluster'
    _require_payload(payload, ('keywords', 'serp_results'), ('keywords',))
    keywords = _validate_keywords(payload)
    serp = _validate_serp(payload, keywords)
    state = _new_state(0)
    findings = []

    unique = []
    normalised_map = {}
    for keyword in keywords:
        normalised = _normalize_keyword(keyword)
        if normalised in normalised_map:
            continue
        normalised_map[normalised] = keyword
        unique.append(keyword)
    duplicates = len(keywords) - len(unique)
    if duplicates:
        findings.append(_finding(
            'low', 'keyword_duplicates_after_normalisation',
            {'submitted': len(keywords), 'unique': len(unique), 'removed': duplicates,
             'normalisation': 'casefold, NFKD, punctuation to space, whitespace collapse'},
            'De-duplicate the keyword list; normalised duplicates were merged.'))

    keys = sorted(unique)
    tokens = {keyword: _keyword_tokens(keyword) for keyword in keys}
    if serp is not None:
        covered = [keyword for keyword in keys if serp.get(keyword)]
        uncovered = [keyword for keyword in keys if not serp.get(keyword)]
        urls = {keyword: tuple(sorted({_normalize_serp_url(value)
                                       for value in serp.get(keyword, [])}))
                for keyword in keys}

        def similarity(left, right):
            if urls[left] and urls[right]:
                return _jaccard(urls[left], urls[right])
            return 0.0

        groups, pairs = _group(keys, similarity, SERP_OVERLAP_THRESHOLD)
        basis = 'user_supplied_serp_url_overlap'
        grouping_label = 'user_supplied_unverified'
        status = 'completed' if not uncovered else 'degraded'
        if uncovered:
            findings.append(_finding(
                'medium', 'serp_results_incomplete',
                {'keywords_with_results': len(covered),
                 'keywords_without_results': _samples(uncovered)},
                'Keywords without supplied results could not be grouped by result '
                'overlap and were left as single-keyword clusters.'))
        findings.append(_finding(
            'info', 'serp_results_user_supplied_unverified',
            {'keywords_with_results': len(covered),
             'max_urls_per_keyword': MAX_SERP_URLS,
             'provenance': 'user_supplied_unverified'},
            'Result URLs were supplied by the caller and are used exactly as given. '
            'They were not fetched, re-queried or verified, and no position, volume or '
            'traffic value is attached to them. Clusters reflect only the overlap in '
            'the lists you provided, at the time and location you captured them.'))
        overlap_evidence = _samples([
            {'keywords': [left, right], 'overlap': round(score, 4),
             'shared_urls': _samples(sorted(set(urls[left]) & set(urls[right])))}
            for left, right, score in pairs])
    else:
        def similarity(left, right):
            return _jaccard(tokens[left], tokens[right])

        groups, pairs = _group(keys, similarity, LEXICAL_OVERLAP_THRESHOLD)
        basis = 'lexical_token_overlap'
        grouping_label = 'lexical_degraded_no_serp_data'
        status = 'degraded'
        urls = {}
        findings.append(_finding(
            'medium', 'lexical_grouping_only_no_serp_data',
            {'basis': basis, 'threshold': LEXICAL_OVERLAP_THRESHOLD,
             'provided_serp_results': False},
            'No serp_results were supplied, so keywords were grouped by shared words '
            'alone. This is a string similarity grouping, not search intent: it cannot '
            'tell that different wordings share one intent, or that identical wordings '
            'have different intents. Supply serp_results, or validate the grouping '
            'against real search data before acting on it.'))
        overlap_evidence = _samples([
            {'keywords': [left, right], 'overlap': round(score, 4),
             'shared_tokens': sorted(set(tokens[left]) & set(tokens[right]))}
            for left, right, score in pairs])

    clusters = []
    for members in groups:
        shared = set(tokens[members[0]])
        for member in members[1:]:
            shared &= set(tokens[member])
        if shared:
            label = ' '.join(sorted(shared))
        elif tokens[members[0]]:
            label = tokens[members[0]][0]
        else:
            label = members[0]
        clusters.append({'label': _clean(label, 120), 'keywords': members,
                         'size': len(members), 'basis': basis,
                         'shared_tokens': sorted(shared),
                         'shared_urls': _samples(sorted(
                             set.intersection(*[set(urls[member]) for member in members])
                             if urls and all(urls.get(member) for member in members) else []))})
    clusters.sort(key=lambda item: (-item['size'], item['label'], item['keywords']))
    singletons = [item['label'] for item in clusters if item['size'] == 1]
    if singletons:
        findings.append(_finding(
            'info', 'single_keyword_clusters',
            {'clusters': _samples(singletons)},
            'These keywords shared no grouping signal with the others and remain '
            'standalone.'))

    evidence = {'keywords_submitted': len(keywords), 'keywords_unique': len(unique),
                'clusters': _samples(clusters, MAX_KEYWORDS),
                'cluster_count': len(clusters),
                'grouping': grouping_label,
                'grouping_basis': basis,
                'threshold': (SERP_OVERLAP_THRESHOLD if serp is not None
                              else LEXICAL_OVERLAP_THRESHOLD),
                'overlaps': overlap_evidence,
                'serp_results_supplied': serp is not None,
                'serp_results_provenance': ('user_supplied_unverified' if serp is not None
                                            else None)}
    limitations = [
        'Grouping is single-link agglomerative over a fixed similarity threshold and is '
        'fully deterministic. A different threshold would produce different clusters.',
        'No search volume, keyword difficulty, CPC, competition, ranking position, '
        'traffic estimate or opportunity score is produced. None of that data is '
        'present in the input and none is invented.',
        'No search engine, keyword tool or ranking API is queried; this operation '
        'performs no network access at all.',
        _NO_METRICS_NOTE]
    if serp is not None:
        limitations.insert(0, 'Clusters derive from caller-supplied result URLs marked '
                              'user_supplied_unverified. They were never fetched or '
                              'verified, and results vary by location, personalisation '
                              'and time.')
    else:
        limitations.insert(0, 'Status is degraded: without serp_results this is lexical '
                              'string grouping only, explicitly not intent clustering '
                              'and not based on live search data.')
    return _result(operation, status,
                   _method(operation, state, ('regex', 'unicodedata'),
                           {'similarity': 'jaccard', 'linkage': 'single',
                            'network_access': False}),
                   evidence, findings, limitations)


_DISPATCH = {
    'seo.page_audit': page_audit,
    'seo.technical': technical,
    'seo.content': content,
    'seo.schema': schema,
    'seo.sitemap': sitemap,
    'seo.local': local,
    'seo.geo': geo,
    'seo.keyword_cluster': keyword_cluster,
}


def analyze(operation, payload, fetcher=None):
    """Run one SEO operation and return its result dict.

    operation: full operation id, e.g. ``seo.page_audit``.
    payload:   ``{'url': ...}`` for URL operations; ``{'keywords': [...],
               'serp_results': {...}}`` (serp_results optional) for
               ``seo.keyword_cluster``.
    fetcher:   ``fetcher(url) -> {'url','status','headers','text','content_type'}``.
               Required by every operation except ``seo.keyword_cluster``. The
               fetcher owns transport, redirects, timeouts and SSRF/allowlist
               enforcement; this module only consumes what it returns.

    Raises SeoError for invalid operations and payloads. Retrieval failures are
    not raised: they degrade the result and are reported as findings and
    limitations.
    """
    return _DISPATCH[_require_operation(operation)](payload, fetcher)
