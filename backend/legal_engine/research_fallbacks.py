"""Optional fallback routes when a primary public_research lookup errors.

Scope and honesty (live-checked 2026-10-24, local network):
- jdih.mahkamahagung.go.id and kepaniteraan.mahkamahagung.go.id answer HTTP 403
  behind a Cloudflare browser challenge to server-side clients. That is a
  denial: it is never bypassed, so these hosts are NOT in the fallback
  allowlist and are never fetched.
- jdih.kemkes.go.id/putusan answers 200 and publishes genuine MA/PN/PT/MK
  judgments as a browse listing. The portal offers no server-side keyword
  search (its search box is a JS/Livewire component; GET ?q= is ignored by
  the server), so the fallback fetches a bounded number of listing pages and
  matches the query words against the published titles client-side. The
  portal requires TLS 1.3: a runtime whose stdlib cannot negotiate TLS 1.3
  (e.g. macOS system Python linked against LibreSSL 2.8.3, max TLS 1.2)
  gets outcome 'error' reported honestly; the connection is never downgraded
  to http or weaker TLS. Portal defect observed: the listing CMS truncates
  long detail slugs, so some published links 404 on their own portal; links
  are stored exactly as published, never rewritten.
- simpul.peradi.id is NOT used for kb.advokat.verify. Its ownership
  provenance versus the primary issuer portal peradi.or.id could not be
  verified: it self-describes as 'Persatuan Advokat Indonesia' while
  peradi.or.id is the portal of 'Perhimpunan Advokat Indonesia' (different
  association names), neither site references the other, and PANDI RDAP
  redacts registrant organizations. Per policy an unverified alternative is
  never used and the blocker is reported instead of worked around. No
  advocate/licensing fallback is active; primary errors pass through
  unchanged and nothing about an advocate lookup is ever cached.

Bounds (in addition to public_research.Research):
- Fallback fetches reuse PortalFetcher with an exact single-host allowlist
  re-checked at connection time, so every redirect hop must land on the one
  allowlisted official host, https only. Client requests never carry URLs;
  only fixed URL templates plus a validated page number are built.
- A bounded page walk (at most MAX_PAGE listing pages) under a wall-clock
  budget, serialized through the inherited bounded semaphore and per-host
  politeness gap.
- The separate fallback cache holds only public record payloads (same fields
  as the primary cache, keyed by a digest, never the raw query text or any
  client name) and is written only for non-licensed collections.
- outcome remains exactly one of 'match', 'no_match', 'error'. When the
  fallback itself cannot run, the primary error result is returned
  unchanged (baseline behavior), so a caller can always distinguish
  error / no_match / match.
"""
import hashlib
import hmac
import json
import re
import sqlite3
import time
from urllib.parse import urlencode

from .common import DomainError, require, now_iso
from .official_network import NetworkError
from .public_research import (
    MAX_LIMIT, MAX_PAGE, MAX_QUERY_CHARS, PortalFetcher,
    PortalStructureError, Research, _clean, _record,
)

FALLBACK_CACHE_SCHEMA_VERSION = 2
FALLBACK_MAX_CACHE_ROWS = 1000
FALLBACK_TOTAL_BUDGET_SECONDS = 45.0
_KEMKES_HOST = 'jdih.kemkes.go.id'
_KEMKES_BASE = 'https://jdih.kemkes.go.id'

FALLBACK_HOST_ALLOWLIST = frozenset((_KEMKES_HOST,))

_WORD_RE = re.compile(r'[a-z0-9]{3,}')
_KEMKES_ANCHOR = re.compile(
    r'<a[^>]+href="(https://jdih\.kemkes\.go\.id/putusan/[^"]+)"[^>]*>(.*?)</a>', re.S)

_KEMKES_COVERAGE = (
    'Fallback terbatas: hanya entri putusan yang diterbitkan JDIH Kementerian Kesehatan '
    '(beberapa halaman arsip awal; kata kunci dicocokkan pada judul terbit). Bukan seluruh '
    'arsip putusan MA/pengadilan dan bukan indeks nasional. Beberapa tautan detail di halaman '
    'daftar terpotong oleh portal itu sendiri dan disimpan apa adanya.')
_KEMKES_NOTE = (
    'Portal menuntut TLS 1.3; runtime tanpa TLS 1.3 pada stdlib mendapat outcome error dan '
    'koneksi tidak pernah diturunkan. Pencarian kata kunci portal bersifat JS; fallback hanya '
    'membaca halaman arsip GET publik.')

ADVOCATE_FALLBACK_BLOCKED_REASON = (
    'simpul.peradi.id tidak dipakai: kepemilikan/provenance terhadap penerbit primer '
    'peradi.or.id tidak dapat diverifikasi (nama organisasi berbeda: "Persatuan Advokat '
    'Indonesia" vs "Perhimpunan Advokat Indonesia" pada peradi.or.id; tidak ada referensi '
    'silang kedua arah; RDAP PANDI menyamarkan pemilik domain; registrar dan era registrasi '
    'berbeda). Sesuai kebijakan, sumber alternatif yang tidak terverifikasi tidak dipakai; '
    'error primer diteruskan tanpa fallback dan tidak ada data advokat yang dicache.')

FALLBACK_PORTALS = {
    'kb.putusan.ma.search': {
        'name': 'JDIH Kementerian Kesehatan — Arsip Putusan (fallback)',
        'host': _KEMKES_HOST,
        'index_form': 'official_published_listing_client_side_matched',
        'kinds': ('mahkamah_agung',),
        'enabled': True,
        'coverage': _KEMKES_COVERAGE,
        'note': _KEMKES_NOTE,
    },
    'kb.putusan.pengadilan.search': {
        'name': 'JDIH Kementerian Kesehatan — Arsip Putusan Pengadilan (fallback)',
        'host': _KEMKES_HOST,
        'index_form': 'official_published_listing_client_side_matched',
        'kinds': ('pengadilan_negeri', 'pengadilan_tinggi'),
        'enabled': True,
        'coverage': _KEMKES_COVERAGE,
        'note': _KEMKES_NOTE,
    },
}

FALLBACK_STATUS = {
    'kb.putusan.ma.search': {
        'available': True, 'portal': _KEMKES_BASE + '/putusan',
        'verified': 'Live 2026-10-24: HTTP 200 dengan entri putusan MA/PN/PT/MK asli; '
                    'TLS 1.3 wajib (klien stdlib tanpa TLS 1.3 gagal dan dilaporkan jujur); '
                    'pencarian kata kunci portal tidak tersedia sisi-server.',
    },
    'kb.putusan.pengadilan.search': {
        'available': True, 'portal': _KEMKES_BASE + '/putusan',
        'verified': 'Live 2026-10-24: HTTP 200 dengan entri putusan PN/PT asli; batasan sama '
                    'dengan jalur MA.',
    },
    'kb.advokat.verify': {
        'available': False, 'portal': None,
        'reason': ADVOCATE_FALLBACK_BLOCKED_REASON,
        'verified': 'Live 2026-10-24: simpul.peradi.id menjawab 200 tetapi provenance '
                    'pemilik tidak terverifikasi; putusan3.mahkamahagung.go.id 403; '
                    'peradi.or.id masih "Kompo Not Fundo".',
    },
}


def fallback_status():
    """Honest per-collection fallback availability, safe to report to Root."""
    return json.loads(json.dumps(FALLBACK_STATUS))


def _court_kind(title):
    lowered = title.lower()
    if 'mahkamah agung' in lowered:
        return 'mahkamah_agung'
    if 'pengadilan tinggi' in lowered:
        return 'pengadilan_tinggi'
    if 'pengadilan negeri' in lowered:
        return 'pengadilan_negeri'
    if 'mahkamah konstitusi' in lowered:
        return 'mahkamah_konstitusi'
    return None


def _parse_kemkes_putusan(text, url):
    """Tolerant parser for the JDIH Kemenkes published judgment listing.

    Accepts only exact-host absolute listing anchors; keeps the published
    link exactly as published (the portal itself truncates some long slugs)
    and fails closed when the structure is not recognized.
    """
    records = []
    seen = set()
    for match in _KEMKES_ANCHOR.finditer(text):
        href, inner = match.group(1), match.group(2)
        title = _clean(inner)
        if href in seen or not title or title.lower() == 'detail' or len(title) < 12:
            continue
        seen.add(href)
        records.append(_record(href, title, title,
                               excerpt_basis='result_title_only',
                               court_kind=_court_kind(title)))
    if not records:
        raise PortalStructureError('kemkes_putusan_listing_not_recognized')
    return records


def _match_words(query):
    return _WORD_RE.findall(query.lower())[:8]


def _title_matches(text, query, number):
    words = _match_words(query)
    if words and not all(word in text for word in words):
        return False
    if number:
        return re.search(r'(?<![a-z0-9])' + re.escape(number.lower()) + r'(?![a-z0-9])',
                         text) is not None
    return bool(words)


class FallbackResearch(Research):
    """Research subclass: optional official fallback when the primary errors.

    Baseline first: super().search() always runs and its result is returned
    unchanged unless it ended in outcome 'error' for a collection that has a
    verified fallback portal. If the fallback itself cannot run, the primary
    error result is returned unchanged.
    """

    def __init__(self, cache_path, ttl_seconds=21600, fetcher_factory=None,fingerprint_key=None):
        super().__init__(cache_path, ttl_seconds=ttl_seconds, fetcher_factory=fetcher_factory,fingerprint_key=fingerprint_key)
        with sqlite3.connect(self.cache_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS fallback_cache('
                       'key TEXT PRIMARY KEY, collection TEXT, epoch REAL, payload TEXT)')
        self.cache_path.chmod(0o600)

    @staticmethod
    def _default_fetcher(host):
        # Same pinned construction as the primary: PortalFetcher re-checks the
        # exact host at connection time, so every redirect hop is enforced.
        # The fallback host itself is verified in _fallback_search via
        # FALLBACK_HOST_ALLOWLIST, never here (primary hosts must still work).
        return PortalFetcher(frozenset((host,)), max_bytes=2097152,
                             budget_seconds=22, timeout_seconds=8)

    def _fallback_key(self,collection, query, limit, year, number):
        body = json.dumps({'collection': collection, 'query': query, 'limit': limit,
                           'year': year, 'number': number, 'engine': 'jdih-kemkes-v1'},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hmac.new(self.fingerprint_key,body.encode(),hashlib.sha256).hexdigest()

    def _fallback_cache_lookup(self, key, ttl):
        if ttl <= 0:
            return None
        with sqlite3.connect(self.cache_path) as db:
            row = db.execute('SELECT epoch, payload FROM fallback_cache WHERE key=?',
                             (key,)).fetchone()
        if row is None:
            return None
        age = time.time() - row[0]
        if age < 0 or age > ttl:
            return None
        try:
            payload = json.loads(row[1])
        except ValueError:
            return None
        if payload.get('schema') != FALLBACK_CACHE_SCHEMA_VERSION:
            return None
        return payload

    def _fallback_cache_store(self, key, collection, result):
        # Public records and portal provenance only; the raw query text is
        # never persisted (the key is a digest). Licensed lookups never reach
        # this fallback, enforced by FALLBACK_PORTALS below.
        payload = {'schema': FALLBACK_CACHE_SCHEMA_VERSION,
                   'outcome': result['outcome'], 'records': result['records'],
                   'portal': result['portal'], 'fetched_at': result['fetched_at'],
                   'raw_sha256': result['raw_sha256'], 'http_status': result['http_status'],
                   'records_seen_before_filter': result['records_seen_before_filter']}
        now = time.time()
        with sqlite3.connect(self.cache_path) as db:
            db.execute('INSERT OR REPLACE INTO fallback_cache VALUES(?,?,?,?)',
                       (key, collection, now, json.dumps(payload, ensure_ascii=False)))
            db.execute('DELETE FROM fallback_cache WHERE epoch < ?', (now - self.ttl_seconds,))
            extra = db.execute('SELECT count(*) FROM fallback_cache').fetchone()[0] \
                - FALLBACK_MAX_CACHE_ROWS
            if extra > 0:
                db.execute('DELETE FROM fallback_cache WHERE key IN '
                           '(SELECT key FROM fallback_cache ORDER BY epoch ASC LIMIT ?)', (extra,))

    def _fallback_search(self, collection, portal, query, limit, year, number):
        require(collection in FALLBACK_PORTALS, 'fallback_not_available', 404)
        require(portal['enabled'], 'fallback_not_available', 403)
        require(portal['host'] in FALLBACK_HOST_ALLOWLIST, 'fallback_host_not_verified', 403)
        require(type(query) is str and 0 < len(query) <= MAX_QUERY_CHARS,
                'invalid_research_query')
        require(type(limit) is int and 1 <= limit <= MAX_LIMIT, 'invalid_research_limit')
        key = self._fallback_key(collection, query, limit, year, number)
        cached = self._fallback_cache_lookup(key, self.ttl_seconds)
        if cached is not None:
            return {'query_sha256': key,
                    **{field: cached.get(field) for field in
                       ('outcome', 'records', 'portal', 'fetched_at', 'raw_sha256',
                        'http_status', 'records_seen_before_filter')},
                    'cache': 'hit'}

        words = _match_words(query)
        deadline = time.monotonic() + FALLBACK_TOTAL_BUDGET_SECONDS
        records = []
        seen_urls = set()
        records_seen = 0
        raw_sha = None
        http_status = None
        pages = 0
        try:
            for page in range(1, MAX_PAGE + 1):
                if time.monotonic() >= deadline:
                    break
                target = portal['host']
                params = {'page': str(page)} if page > 1 else {}
                url = _KEMKES_BASE + '/putusan' + ('?' + urlencode(params) if params else '')
                fetcher = self._fetcher_factory(target)
                with self.slots:
                    self._throttle(target)
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    if hasattr(fetcher,'budget_seconds'):fetcher.budget_seconds=min(fetcher.budget_seconds,remaining)
                    response = fetcher.fetch(url)
                http_status = response['status']
                raw_sha = hashlib.sha256(response['body_bytes']).hexdigest()
                require(http_status == 200, 'fallback_portal_http_error', 503)
                pages += 1
                page_records = _parse_kemkes_putusan(response['text'], url)
                fresh = [r for r in page_records if r['source_url'] not in seen_urls]
                if not fresh:
                    break
                for record in fresh:
                    seen_urls.add(record['source_url'])
                    records_seen += 1
                    haystack = (record['title'] + ' ' + record['source_url']).lower()
                    kind = record.get('court_kind')
                    if kind not in portal['kinds']:
                        continue
                    if year and not re.search(r'(?<![0-9])'+str(year)+r'(?![0-9])',record['title']):continue
                    if _title_matches(haystack, query, number):
                        records.append(record)
                if len(records) >= limit:
                    break
        except DomainError:
            return None
        except NetworkError:
            return None
        except PortalStructureError:
            if pages == 0:
                return None

        fetched_at = now_iso()
        for record in records:
            record['fetched_at'] = fetched_at
        records = records[:limit]
        result = {
            'query_sha256': key, 'outcome': 'match' if records else 'no_match',
            'records': records,
            'portal': {'name': portal['name'], 'url': _KEMKES_BASE + '/putusan',
                       'host': _KEMKES_HOST,
        'index_form': 'official_published_listing_client_side_matched', 'official': True, 'fallback': True,
                       'coverage': portal['coverage'], 'note': portal['note'],
                       'pages_fetched': pages},
            'fetched_at': fetched_at, 'raw_sha256': raw_sha, 'http_status': http_status,
            'records_seen_before_filter': records_seen, 'cache': 'miss',
        }
        self._fallback_cache_store(key, collection, result)
        if records:
            self._index_store(collection, records)
        return result

    def search(self, collection, query, limit=5, page=None, year=None, kind=None,
               number=None, ttl_seconds=None):
        primary = super().search(collection, query, limit=limit, page=page, year=year,
                                 kind=kind, number=number, ttl_seconds=ttl_seconds)
        if primary.get('outcome') != 'error':
            return primary
        portal = FALLBACK_PORTALS.get(collection)
        if portal is None or not portal.get('enabled'):
            return primary
        fallback = self._fallback_search(collection, portal, query, limit, year, number)
        return fallback if fallback is not None else primary
