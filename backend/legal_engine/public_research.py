"""Bounded public official-index research for legal discovery. Read-only fetch of
fixed official portals; a fetched record is provenance, never current-law or
identity certification, and excerpts are research snippets, not statutory quotes.

Safety and bounds (in addition to official_network.SafeFetcher):
- PortalFetcher pins an exact per-collection host allowlist enforced at connection
  time, so every redirect hop must land on the same allowlisted official host.
  Client requests never carry URLs; only fixed URL templates plus validated
  query parameters are ever built. Responses are never executed.
- Research.search() serializes portal access through a bounded semaphore, keeps a
  per-host politeness gap, and inherits SafeFetcher byte/redirect/wall-clock
  budgets. Network runs only where the runtime places it (outside the global
  DB writer; the root runtime owns that arrangement).
- Persistence holds only public documents/index rows keyed by content, never the
  raw query text or client names; cache keys are content hashes and cache entries
  expire after a fixed TTL. Verification collections (kb.advokat.verify) are
  never persisted.
- outcome is always one of 'match', 'no_match', 'error'; portal denial or
  structural change yields 'error', never a fabricated empty success.

Live portal status observed 2026-10-24 during endpoint research (recorded
honestly in each portal's note; the smoke runner re-checks current status):
- putusan3.mahkamahagung.go.id denies server-side clients (HTTP 403 Access
  Denied on http and https, multiple clients) for kb.putusan.ma.search and
  kb.putusan.pengadilan.search.
- peradi.or.id member search answers 'Kompo Not Fundo' (broken component) for
  kb.advokat.verify; the handler reports error instead of inventing a registry.
- The remaining portals answered 200 with parseable official result structures.
"""
import hashlib
import hmac
import os
import json
import re
import sqlite3
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlencode

from .common import DomainError, require, now_iso
from .official_network import NetworkError, SafeFetcher

COLLECTIONS = (
    'kb.putusan.ma.search', 'kb.putusan.mk.search', 'kb.putusan.pengadilan.search',
    'kb.pp.search', 'kb.perpres.search', 'kb.permendagri.search', 'kb.perda.search',
    'kb.sirclo.search', 'kb.intl.law.search', 'kb.intl.case.search',
    'kb.article.legal.search', 'kb.konsil.yudisial', 'kb.advokat.verify', 'kb.lbh.search',
)

HOST_ALLOWLIST = frozenset({
    'putusan3.mahkamahagung.go.id', 'peraturan.go.id', 'jdih.komdigi.go.id',
    'www.ecolex.org', 'api.crossref.org', 'www.komisiyudisial.go.id',
    'peradi.or.id', 'ylbhi.or.id',
})

MAX_QUERY_CHARS = 500
MAX_LIMIT = 10
MAX_PAGE = 5
MAX_CACHE_ROWS = 4000
CACHE_SCHEMA_VERSION = 1
PORTAL_GAP_SECONDS = 2.0
DEFAULT_TTL_SECONDS = 21600

_PERATURAN = 'https://peraturan.go.id'
_TAG_RE = re.compile(r'<[^>]+>')
_SLUG_YEAR = re.compile(r'tahun-(\d{4})')
_ID_ANCHOR = re.compile(r'<a[^>]+href="(/id/[A-Za-z0-9][^"]*)"[^>]*>(.*?)</a>', re.S)
_PUTUSAN_ANCHOR = re.compile(r'<a[^>]+href="(/putusan/[^"]+)"[^>]*>(.*?)</a>', re.S)
_PUTUSAN_PDF = re.compile(r'href="(/files\d*/[^"]+\.pdf)"', re.I)
_KOMDIGI_VIEW = re.compile(r'href="(https://jdih\.komdigi\.go\.id/produk_hukum/view/id/(\d+)/t/[^"?]+)[^"]*"', re.I)
_KOMDIGI_TITLE = re.compile(r'produk-hukum-title[^>]*>(.*?)</h5>', re.S | re.I)
_KOMDIGI_DESC = re.compile(r'class="[^"]*desc[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
_KOMDIGI_DATE = re.compile(r'class="[^"]*date[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
_ECOLEX_ITEM = re.compile(r'<h3 class="search-result-title">\s*<a href="(/details/([a-z_-]+)/[^"]+)"[^>]*>(.*?)</a>', re.S)
_ECOLEX_TYPE = re.compile(r'<strong class="sr-type">([^<]*)</strong>')
_ECOLEX_DATE = re.compile(r'title="Date:\s*([^"]*)"')
_ECOLEX_KEYWORDS = re.compile(r'<span class="collapse-keywords">([^<]*)</span>')
_KY_ANCHOR = re.compile(r'<a[^>]+href="(/frontend/[a-z_]+/[A-Za-z0-9][^"]*)"[^>]*>(.*?)</a>', re.S)
_YLBHI_ITEM = re.compile(r'<li class="elementor-icon-list-item">\s*<a href="([^"]+)"[^>]*>.*?'
                         r'elementor-icon-list-text">([^<]+)</span>', re.S)
_ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
_CELL_RE = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S)
_JATS_TAG = re.compile(r'</?[A-Za-z][^>]*>')


class PortalStructureError(Exception):
    """The portal answered but its result structure was not recognized."""


class PortalFetcher(SafeFetcher):
    """SafeFetcher pinned to one exact official host, https only.

    The allowlist is re-checked here for every connection, so every redirect
    hop is enforced before any socket is opened to it.
    """

    def __init__(self, allowed_hosts, **bounds):
        require(isinstance(allowed_hosts, frozenset) and bool(allowed_hosts), 'invalid_portal_allowlist')
        super().__init__(**bounds)
        self.allowed_hosts = frozenset(str(h).lower() for h in allowed_hosts)

    def _open_connection(self, address, port, hostname, https, timeout):
        require(https and hostname in self.allowed_hosts, 'research_portal_not_allowlisted', 403)
        return super()._open_connection(address, port, hostname, https, timeout)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag in ('p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'td', 'th', 'time'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _clean(value, limit=450):
    text = re.sub(r'\s+', ' ', _TAG_RE.sub('', value or '')).strip()
    return text[:limit]


def _record(url, title, excerpt, **extra):
    # Published links are stored exactly as the portal published them (http or https);
    # the research fetch itself always runs https through PortalFetcher.
    require(type(url) is str and url.startswith(('https://', 'http://')), 'research_record_url_invalid')
    body = json.dumps({'source_url': url, 'title': title, 'excerpt': excerpt},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    rec = {'source_url': url, 'title': title, 'excerpt': excerpt,
           'sha256': hashlib.sha256(body.encode()).hexdigest(), 'fetched_at': None}
    rec.update(extra)
    return rec


def _same_site(base_url, href, allowed_host):
    absolute = urljoin(base_url, href)
    host = (urlsplit(absolute).hostname or '').lower()
    if host != allowed_host:
        return None
    return absolute


def _parse_cariglobal(text, url):
    records = []
    seen = set()
    for match in _ID_ANCHOR.finditer(text):
        slug, inner = match.group(1), match.group(2)
        title = _clean(inner)
        if slug in seen or slug == '/id/#' or len(title) < 8:
            continue
        seen.add(slug)
        kind = slug.split('/')[2].split('-no-')[0]
        year_match = _SLUG_YEAR.search(slug)
        records.append(_record(
            _PERATURAN + quote(slug), title, title,
            excerpt_basis='result_title_only', regulation_slug=slug_base(slug),
            regulation_kind=kind, year=int(year_match.group(1)) if year_match else None))
    return records


def slug_base(slug):
    return slug.split('/')[2] if slug.startswith('/id/') else slug


def portal_is_scoped_by_fields(collection):
    return PORTALS[collection]['params'] in (_mk_params, _mk_page_params)


def portal_accepts_empty_query(collection, scope):
    if portal_is_scoped_by_fields(collection):
        return True
    return collection in _REGULATION_COLLECTIONS and _typed_scope(scope)


def _parse_putusan_mk(text, url):
    records = []
    seen = set()
    for match in _PUTUSAN_ANCHOR.finditer(text):
        slug, inner = match.group(1), match.group(2)
        title = _clean(inner)
        if slug in seen or len(title) < 12:
            continue
        seen.add(slug)
        start = max(0, match.start() - 300)
        pdf = _PUTUSAN_PDF.search(text[start:match.end() + 700])
        year_match = _SLUG_YEAR.search(slug)
        excerpt = 'Dokumen putusan dipublikasikan pada portal resmi; judul: ' + title
        records.append(_record(
            _PERATURAN + quote(slug), title, excerpt,
            excerpt_basis='result_title_and_document_links',
            document_url=_PERATURAN + quote(pdf.group(1)) if pdf else None,
            year=int(year_match.group(1)) if year_match else None))
    return records


def _parse_putusan_ma(text, url):
    """Tolerant parser for the putusan3 search/directory result list.

    The portal currently denies server clients, so this structure could not be
    re-verified live; it parses only clearly-shaped result anchors and fails
    closed (PortalStructureError) when none are present.
    """
    records = []
    seen = set()
    for match in re.finditer(r'<a[^>]+href="(/(?:direktori|detail|upload)/[^"]+)"[^>]*>(.*?)</a>', text, re.S):
        slug, inner = match.group(1), match.group(2)
        title = _clean(inner)
        if slug in seen or len(title) < 12:
            continue
        seen.add(slug)
        records.append(_record('https://putusan3.mahkamahagung.go.id' + quote(slug), title, title,
                               excerpt_basis='result_title_only'))
    if not records:
        raise PortalStructureError('putusan3_result_structure_not_recognized')
    return records


def _parse_komdigi(text, url):
    records = []
    seen = set()
    blocks = text.split('card-produk-hukum-list')
    for block in blocks[1:]:
        view = _KOMDIGI_VIEW.search(block)
        if view is None:
            continue
        view_url, view_id = view.group(1), view.group(2)
        if view_id in seen:
            continue
        seen.add(view_id)
        title_match = _KOMDIGI_TITLE.search(block)
        desc_match = _KOMDIGI_DESC.search(block)
        date_match = _KOMDIGI_DATE.search(block)
        title = _clean(title_match.group(1)) if title_match else view_url.rstrip('/').rsplit('/', 1)[-1]
        excerpt = _clean(desc_match.group(1), 600) if desc_match else title
        records.append(_record(
            view_url.split('?')[0], title, excerpt,
            excerpt_basis='portal_snippet', document_id=view_id,
            published_date=_clean(date_match.group(1), 60) if date_match else None,
            circulation_category='surat_edaran_jdih_internal'))
    return records


def _parse_ecolex(text, url):
    records = []
    seen = set()
    for match in _ECOLEX_ITEM.finditer(text):
        href, kind, inner = match.group(1), match.group(2), match.group(3)
        key = href.split('?')[0]
        if key in seen:
            continue
        seen.add(key)
        title = _clean(inner)
        tail = text[match.end():match.end() + 900]
        type_match = _ECOLEX_TYPE.search(tail)
        date_match = _ECOLEX_DATE.search(tail)
        keyword_match = _ECOLEX_KEYWORDS.search(tail)
        excerpt = 'Keywords: ' + _clean(keyword_match.group(1), 350) if keyword_match else title
        records.append(_record(
            'https://www.ecolex.org' + quote(key), title, excerpt,
            excerpt_basis='portal_keywords_or_title', document_kind=kind,
            entry_type=_clean(type_match.group(1), 40) if type_match else None,
            entry_date=_clean(date_match.group(1), 60) if date_match else None))
    return records


def _parse_crossref(text, url):
    try:
        payload = json.loads(text)
        items = payload['message']['items']
    except (ValueError, KeyError, TypeError):
        raise PortalStructureError('crossref_payload_not_recognized') from None
    if not isinstance(items, list):
        raise PortalStructureError('crossref_payload_not_recognized')
    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _clean(' '.join(item.get('title') or []), 400)
        target = item.get('URL')
        if not title or not isinstance(target, str):
            continue
        container = _clean(' '.join(item.get('container-title') or []), 200)
        year = None
        issued = item.get('issued') or {}
        parts = issued.get('date-parts') if isinstance(issued, dict) else None
        if parts and parts[0] and isinstance(parts[0][0], int):
            year = parts[0][0]
        abstract = _clean(_JATS_TAG.sub(' ', item.get('abstract') or ''), 600)
        excerpt = abstract if abstract else (container + ' (' + str(year) + ')' if container else title)
        records.append(_record(
            target, title, excerpt, excerpt_basis='abstract_or_container',
            container=container or None, year=year,
            doi=_clean(item.get('DOI') or '', 120) or None,
            work_type=_clean(item.get('type') or '', 60) or None))
    return records


_KY_KINDS = (('announcement_detail', 'announcement'), ('pers_release_detail', 'press_release'),
             ('news_detail', 'news'), ('publication', 'publication'), ('static_content', 'page'))
_KY_RANK = {'announcement': 0, 'press_release': 1, 'news': 2, 'publication': 3, 'page': 4}


def _parse_ky(text, url):
    start = text.find('Hasil Pencarian')
    window = text[start:] if start >= 0 else text
    records = []
    seen = set()
    for match in _KY_ANCHOR.finditer(window):
        href, inner = match.group(1), match.group(2)
        title = _clean(inner)
        if href in seen or len(title) < 15:
            continue
        seen.add(href)
        kind = 'page'
        for marker, label in _KY_KINDS:
            if marker in href:
                kind = label
                break
        records.append(_record(
            'https://www.komisiyudisial.go.id' + quote(href), title, title,
            excerpt_basis='result_title_only', ky_content_kind=kind))
    records.sort(key=lambda r: _KY_RANK.get(r['ky_content_kind'], 9))
    return records


def _parse_ylbhi(text, url):
    records = []
    seen = set()
    for match in _YLBHI_ITEM.finditer(text):
        href, name = match.group(1).strip(), _clean(match.group(2), 200)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        if not href.startswith('http'):
            continue
        records.append(_record(
            href, name, 'Entri direktori terbit: ' + name,
            excerpt_basis='published_directory_entry',
            directory='ylbhi_member_offices'))
    if not records:
        raise PortalStructureError('lbh_directory_structure_not_recognized')
    return records


def _parse_peradi(text, url):
    if 'kompo not fundo' in text.lower():
        raise PortalStructureError('search_component_unavailable')
    records = []
    for row in _ROW_RE.finditer(text):
        cells = [_clean(cell, 200) for cell in _CELL_RE.findall(row.group(1))]
        cells = [cell for cell in cells if cell]
        if len(cells) < 1:
            continue
        records.append(_record(
            'https://peradi.or.id/index.php/search', cells[0], ' | '.join(cells[:8]),
            excerpt_basis='directory_row', identifier_fields=cells[:8]))
    return records


_REGULATION_COLLECTIONS = frozenset(('kb.pp.search', 'kb.perpres.search',
                                     'kb.permendagri.search', 'kb.perda.search'))


def _p(name, host, path, index_form, coverage, parse, params, note='',
       slug_prefix=None, cache_results=True, persist_index=True, search_matches=None):
    path_fn = path if callable(path) else (lambda scope: path)
    return {'name': name, 'host': host, 'path': path_fn, 'index_form': index_form,
            'coverage': coverage, 'parse': parse, 'params': params, 'note': note,
            'slug_prefix': slug_prefix, 'cache_results': cache_results,
            'persist_index': persist_index, 'search_matches': search_matches}


def _cariglobal_params(scope):
    params = {'PeraturanSearch[idglobal]': scope['query'], 'page': str(scope['page'])}
    return params


def _typed_scope(scope):
    """True when the lookup is identifier-scoped (number and/or year)."""
    return bool(scope['number'] or scope['year'])


def _peraturan_typed(form, base_path):
    """Per-type server-side search (/pp, /perpres, /permen, /perda).

    GET keyword search on peraturan.go.id is global-only, so text queries run
    through /cariglobal and are type-filtered client-side; number/year lookups
    are scoped server-side on the per-type page (verified live 2026-10-24).
    """
    def params(scope):
        if _typed_scope(scope):
            out = {}
            if scope['number']:
                out[form + '[nomor]'] = scope['number']
            if scope['year']:
                out[form + '[tahun]'] = str(scope['year'])
            return out
        return _cariglobal_params(scope)
    return params


def _peraturan_typed_path(base_path):
    return lambda scope: base_path if _typed_scope(scope) else '/cariglobal'


def _putusan3_params(scope):
    return {'key': scope['query'], 'page': str(scope['page'])}


def _mk_params(scope):
    params = {}
    if scope['query']:
        params['PutusanmkSearch[tentang]'] = scope['query']
    if scope['number']:
        params['PutusanmkSearch[nomor]'] = scope['number']
    if scope['year']:
        params['PutusanmkSearch[tahun]'] = str(scope['year'])
    require(params, 'empty_research_scope')
    return params


def _mk_page_params(scope):
    params = _mk_params(scope)
    if scope['page'] > 1:
        params['page'] = str(scope['page'])
    return params


def _komdigi_params(scope):
    params = {'tentang': scope['query'], 'kategori': '11'}
    if scope['year']:
        params['tahun'] = str(scope['year'])
    return params


def _ecolex_params(scope):
    return {'q': scope['query'], 'type': scope['kind']}


def _crossref_params(scope):
    return {'query': scope['query'], 'rows': str(scope['limit']),
            'select': 'title,URL,container-title,issued,abstract,DOI,type'}


def _ky_params(scope):
    return {'query': scope['query']}


def _peradi_params(scope):
    return {'aks': 'cari', 'cari': scope['query']}


def _no_params(scope):
    return {}


PORTALS = {
    'kb.putusan.ma.search': _p(
        'Direktori Putusan Mahkamah Agung', 'putusan3.mahkamahagung.go.id', '/search',
        'official_portal_search',
        'Hanya direktur putusan terbit MA pada portal resmi; bukan seluruh arsip peradilan.',
        _parse_putusan_ma, _putusan3_params,
        note='Portal menolak klien sisi-server (HTTP 403 Access Denied, diverifikasi 2026-10-24); '
             'handler melaporkan error sampai portal mengizinkan akses.'),
    'kb.putusan.mk.search': _p(
        'Penerbitan Putusan MK — JDIH Kemenkumham', 'peraturan.go.id', '/putusan',
        'official_portal_search',
        'Hanya putusan MK yang diterbitkan pada portal peraturan.go.id; bukan seluruh arsip MK.',
        _parse_putusan_mk, _mk_page_params),
    'kb.putusan.pengadilan.search': _p(
        'Direktori Putusan Pengadilan (MA)', 'putusan3.mahkamahagung.go.id', '/search',
        'official_portal_search',
        'Hanya putusan pengadilan di bawah MA yang terbit pada direktori resmi; bukan seluruh arsip.',
        _parse_putusan_ma, _putusan3_params,
        note='Portal menolak klien sisi-server (HTTP 403 Access Denied, diverifikasi 2026-10-24); '
             'handler melaporkan error sampai portal mengizinkan akses.'),
    'kb.pp.search': _p(
        'Database Peraturan — JDIH Kemenkumham', 'peraturan.go.id',
        _peraturan_typed_path('/pp'),
        'official_global_index_filtered',
        'Nomor/tahun: pencarian terbatas PP sisi-server. Kata kunci: indeks global resmi '
        'difilter ke slug PP; cakupan mengikuti portal dan bisa no_match di halaman awal.',
        _parse_cariglobal, _peraturan_typed('PeraturanSearch', '/pp'), slug_prefix='pp-'),
    'kb.perpres.search': _p(
        'Database Peraturan — JDIH Kemenkumham', 'peraturan.go.id',
        _peraturan_typed_path('/perpres'),
        'official_global_index_filtered',
        'Nomor/tahun: pencarian terbatas Perpres sisi-server. Kata kunci: indeks global resmi '
        'difilter ke slug Perpres; cakupan mengikuti portal.',
        _parse_cariglobal, _peraturan_typed('PeraturanSearch', '/perpres'), slug_prefix='perpres-'),
    'kb.permendagri.search': _p(
        'Database Peraturan — JDIH Kemenkumham', 'peraturan.go.id',
        _peraturan_typed_path('/permen'),
        'official_global_index_filtered',
        'Nomor/tahun: pencarian peraturan menteri sisi-server lalu difilter ke slug Permendagri '
        '(portal JDIH Kemendagiri langsung tidak tersedia dari jaringan ini). Kata kunci: indeks '
        'global resmi difilter ke slug Permendagri.',
        _parse_cariglobal, _peraturan_typed('PeraturanSearch', '/permen'), slug_prefix='permendagri-'),
    'kb.perda.search': _p(
        'Database Peraturan — JDIH Kemenkumham', 'peraturan.go.id',
        _peraturan_typed_path('/perda'),
        'official_global_index_filtered',
        'Nomor/tahun: pencarian Perda sisi-server (provinsi/kabupaten/kota dengan nomor sama '
        'semua tampil). Kata kunci: indeks global resmi difilter ke slug Perda.',
        _parse_cariglobal, _peraturan_typed('PerdaSearch', '/perda'), slug_prefix='perda-'),
    'kb.sirclo.search': _p(
        'JDIH Komdigi — Sirkulasi internal (Surat Edaran)', 'jdih.komdigi.go.id',
        '/produk_hukum/pencarian',
        'official_portal_search',
        'Hanya sirkulasi internal JDIH Komdigi (kategori Surat Edaran); bukan perusahaan SIRCLO.',
        _parse_komdigi, _komdigi_params),
    'kb.intl.law.search': _p(
        'ECOLEX (FAO/IUCN/UNEP)', 'www.ecolex.org', '/result/',
        'official_portal_search',
        'Hanya treaty/peraturan terindeks ECOLEX; bukan seluruh hukum internasional.',
        _parse_ecolex, _ecolex_params),
    'kb.intl.case.search': _p(
        'ECOLEX court decisions (FAO/IUCN/UNEP)', 'www.ecolex.org', '/result/',
        'official_portal_search',
        'Hanya putusan pengadilan terindeks ECOLEX; ICJ/ICC/ECTHR tidak dapat diakses sisi-server.',
        _parse_ecolex, _ecolex_params),
    'kb.article.legal.search': _p(
        'Crossref metadata', 'api.crossref.org', '/works',
        'official_metadata_api',
        'Hanya metadata artikel ber-DOI terindeks Crossref; bukan jurnal seluruh dunia.',
        _parse_crossref, _crossref_params),
    'kb.konsil.yudisial': _p(
        'Pencarian situs Komisi Yudisial', 'www.komisiyudisial.go.id',
        '/frontend/searching_process',
        'official_portal_search',
        'Hanya konten terbit KY (berita, pengumuman, rilis pers, profil); bukan putusan seluruh laporan.',
        _parse_ky, _ky_params),
    'kb.advokat.verify': _p(
        'Pencarian anggota PERADI', 'peradi.or.id', '/index.php/search',
        'official_directory_exact_lookup',
        'Pencocokan identifier eksak pada direktori terbit saja; bukan sertifikasi identitas.',
        _parse_peradi, _peradi_params, cache_results=False, persist_index=False,
        note='Komponen pencarian portal PERADI menjawab tidak tersedia (diverifikasi 2026-10-24); '
             'handler melaporkan error dan tidak pernah membuat registry sendiri.',
        search_matches='exact_identifier'),
    'kb.lbh.search': _p(
        'Direktori LBH terbit YLBHI', 'ylbhi.or.id', '/',
        'official_published_directory',
        'Hanya direktori kantor LBH yang diterbitkan YLBHI; tanpa estimasi jarak.',
        _parse_ylbhi, _no_params, search_matches='query_words'),
}

# Cached payloads hold only public document data plus stable identifiers; the
# request URL (which embeds the query text) is never persisted.
_CACHED_FIELDS = ('query_sha256', 'outcome', 'records', 'fetched_at',
                  'raw_sha256', 'http_status', 'error', 'records_seen_before_filter')


class Research:
    """Bounded read-only research adapter over fixed official public indexes."""

    def __init__(self, cache_path, ttl_seconds=DEFAULT_TTL_SECONDS, fetcher_factory=None, fingerprint_key=None):
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fingerprint_key=fingerprint_key or os.urandom(32)
        require(type(self.fingerprint_key) is bytes and len(self.fingerprint_key)==32,'research_fingerprint_key_required')
        self.ttl_seconds = int(ttl_seconds)
        require(self.ttl_seconds >= 1, 'invalid_research_ttl')
        self.slots = threading.BoundedSemaphore(2)
        self._host_lock = threading.Lock()
        self._host_last = {}
        self._fetcher_factory = fetcher_factory or self._default_fetcher
        with sqlite3.connect(self.cache_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS research_cache('
                       'key TEXT PRIMARY KEY, collection TEXT, epoch REAL, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS index_documents('
                       'collection TEXT, source_url TEXT, title TEXT, excerpt TEXT,'
                       'sha256 TEXT, fetched_at TEXT, PRIMARY KEY(collection, source_url))')
        self.cache_path.chmod(0o600)

    @staticmethod
    def _default_fetcher(host):
        return PortalFetcher(frozenset((host,)), max_bytes=2097152,
                             budget_seconds=22, timeout_seconds=8)

    def _cache_lookup(self, key, ttl):
        if ttl <= 0:
            return None
        with sqlite3.connect(self.cache_path) as db:
            row = db.execute('SELECT epoch, payload FROM research_cache WHERE key=?', (key,)).fetchone()
        if row is None:
            return None
        age = time.time() - row[0]
        if age < 0 or age > ttl:
            return None
        try:
            payload = json.loads(row[1])
        except ValueError:
            return None
        if payload.get('schema') != CACHE_SCHEMA_VERSION:
            return None
        return payload

    def _cache_store(self, key, collection, result):
        payload = {'schema': CACHE_SCHEMA_VERSION,
                   **{field: result.get(field) for field in _CACHED_FIELDS}}
        now = time.time()
        with sqlite3.connect(self.cache_path) as db:
            db.execute('INSERT OR REPLACE INTO research_cache VALUES(?,?,?,?)',
                       (key, collection, now, json.dumps(payload, ensure_ascii=False)))
            db.execute('DELETE FROM research_cache WHERE epoch < ?', (now - self.ttl_seconds,))
            extra = db.execute('SELECT count(*) FROM research_cache').fetchone()[0] - MAX_CACHE_ROWS
            if extra > 0:
                db.execute('DELETE FROM research_cache WHERE key IN '
                           '(SELECT key FROM research_cache ORDER BY epoch ASC LIMIT ?)', (extra,))

    def _index_store(self, collection, records):
        rows = [(collection, r['source_url'], r['title'], r['excerpt'], r['sha256'],
                 r['fetched_at'] or now_iso()) for r in records]
        with sqlite3.connect(self.cache_path) as db:
            db.executemany('INSERT OR REPLACE INTO index_documents VALUES(?,?,?,?,?,?)', rows)
            extra = db.execute('SELECT count(*) FROM index_documents').fetchone()[0] - MAX_CACHE_ROWS
            if extra > 0:
                db.execute('DELETE FROM index_documents WHERE rowid IN '
                           '(SELECT rowid FROM index_documents ORDER BY rowid ASC LIMIT ?)', (extra,))

    def _throttle(self, host):
        with self._host_lock:
            last = self._host_last.get(host)
            wait = 0.0 if last is None else max(0.0, PORTAL_GAP_SECONDS - (time.monotonic() - last))
            self._host_last[host] = time.monotonic() + wait
        if wait > 0:
            time.sleep(min(wait, PORTAL_GAP_SECONDS))

    def _scope_digest(self,collection, query, scope, limit):
        body = json.dumps({'collection': collection, 'query': query, 'scope': scope, 'limit': limit},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hmac.new(self.fingerprint_key,body.encode(),hashlib.sha256).hexdigest()

    def search(self, collection, query, limit=5, page=None, year=None, kind=None,
               number=None, ttl_seconds=None):
        require(collection in PORTALS, 'unknown_research_collection', 404)
        # MK and per-type regulation pages accept identifier-only lookups
        # (number/year without query text); everything else needs a query.
        require(type(query) is str and 0 <= len(query) <= MAX_QUERY_CHARS, 'invalid_research_query')
        require(type(limit) is int and 1 <= limit <= MAX_LIMIT, 'invalid_research_limit')
        require(page is None or (type(page) is int and 1 <= page <= MAX_PAGE), 'invalid_research_page')
        require(year is None or (type(year) is int and 1950 <= year <= 2106), 'invalid_research_year')
        require(number is None or (type(number) is str and 1 <= len(number) <= 30), 'invalid_research_number')
        portal = PORTALS[collection]
        scope = {'page': page or 1, 'year': year, 'kind': kind, 'number': number}
        require(query or portal_accepts_empty_query(collection, scope), 'invalid_research_query')
        if portal['params'] is _ecolex_params:
            kind = kind or 'treaty' if collection == 'kb.intl.law.search' else kind or 'court_decision'
            require(kind in ('treaty', 'legislation', 'court_decision'), 'invalid_research_kind')
        else:
            require(kind is None or type(kind) is str, 'invalid_research_kind')
        key = self._scope_digest(collection, query, scope, limit)
        cached = self._cache_lookup(key, self.ttl_seconds if ttl_seconds is None else ttl_seconds)
        if cached is not None:
            return {'cache': 'hit',
                    **{field: cached.get(field) for field in _CACHED_FIELDS},
                    'portal': {'name': portal['name'], 'url': 'https://' + portal['host'],
                               'host': portal['host'], 'official': True}}

        full_scope = {'query': query, **scope, 'limit': limit}
        params = portal['params'](full_scope)
        path = portal['path'](scope)
        url = 'https://' + portal['host'] + path + ('?' + urlencode(params) if params else '')
        fetcher = self._fetcher_factory(portal['host'])
        raw_sha = None
        http_status = None
        try:
            with self.slots:
                self._throttle(portal['host'])
                response = fetcher.fetch(url)
                http_status = response['status']
                raw_sha = hashlib.sha256(response['body_bytes']).hexdigest()
                require(http_status == 200, 'portal_http_error', 503)
                text = response['text']
        except DomainError as e:
            return self._error_result(key, collection, url, e.code, http_status, raw_sha)
        except NetworkError as e:
            return self._error_result(key, collection, url, e.code, http_status, raw_sha)

        try:
            records = portal['parse'](text, url)
        except PortalStructureError as e:
            return self._error_result(key, collection, url, 'portal_' + str(e), http_status, raw_sha)
        except DomainError:
            return self._error_result(key, collection, url, 'portal_parse_failed', http_status, raw_sha)

        records_seen = len(records)
        if portal['slug_prefix']:
            records = [r for r in records if (r.get('regulation_slug') or '').startswith(portal['slug_prefix'])]
        if year:
            records = [r for r in records if r.get('year') == year]
        if collection == 'kb.article.legal.search' and year:
            records = [r for r in records if r.get('year') == year]
        if portal['search_matches'] == 'query_words':
            words = [w for w in re.findall(r'[a-z0-9]{3,}', query.lower())][:8]
            records = [r for r in records if words and
                       all(w in (r['title'] + ' ' + r['source_url']).lower() for w in words)]
        fetched_at = now_iso()
        for r in records:
            r['fetched_at'] = fetched_at
        result = {
            'query_sha256': key, 'outcome': 'match' if records else 'no_match',
            'records': records[:limit], 'portal': {'name': portal['name'], 'url': url,
                                                   'host': portal['host'], 'official': True},
            'fetched_at': fetched_at, 'raw_sha256': raw_sha, 'http_status': http_status,
            'records_seen_before_filter': records_seen, 'cache': 'miss',
        }
        if portal['cache_results']:
            self._cache_store(key, collection, result)
        if portal['persist_index'] and records:
            self._index_store(collection, records)
        return result

    @staticmethod
    def _error_result(key, collection, url, code, http_status, raw_sha):
        return {'query_sha256': key, 'outcome': 'error',
                'records': [], 'portal': {'url': url, 'official': True},
                'fetched_at': now_iso(), 'raw_sha256': raw_sha, 'http_status': http_status,
                'error': {'code': str(code)[:80]}, 'records_seen_before_filter': 0, 'cache': 'miss'}


def directory_documents(research, collection, limit=50):
    """Read back previously fetched public index rows for one collection."""
    require(collection in PORTALS, 'unknown_research_collection', 404)
    require(type(limit) is int and 1 <= limit <= MAX_CACHE_ROWS, 'invalid_research_limit')
    with sqlite3.connect(research.cache_path) as db:
        rows = db.execute('SELECT source_url, title, excerpt, sha256, fetched_at FROM index_documents '
                          'WHERE collection=? ORDER BY fetched_at DESC LIMIT ?', (collection, limit)).fetchall()
    return [{'source_url': r[0], 'title': r[1], 'excerpt': r[2], 'sha256': r[3], 'fetched_at': r[4]}
            for r in rows]


def _smoke():  # live network, research only
    samples = {
        'kb.putusan.ma.search': {'query': 'kecelakaan'},
        'kb.putusan.mk.search': {'query': '', 'number': '92', 'year': 2024},
        'kb.putusan.pengadilan.search': {'query': 'wanprestasi'},
        'kb.pp.search': {'query': '', 'number': '18', 'year': 2021},
        'kb.perpres.search': {'query': 'perlindungan anak'},
        'kb.permendagri.search': {'query': '', 'number': '74', 'year': 2022},
        'kb.perda.search': {'query': '', 'number': '8', 'year': 2025},
        'kb.sirclo.search': {'query': 'panduan'},
        'kb.intl.law.search': {'query': 'climate change', 'kind': 'treaty'},
        'kb.intl.case.search': {'query': 'fisheries jurisdiction', 'kind': 'court_decision'},
        'kb.article.legal.search': {'query': 'hukum pidana indonesia'},
        'kb.konsil.yudisial': {'query': 'sanksi hakim'},
        'kb.advokat.verify': {'query': 'A.123456'},
        'kb.lbh.search': {'query': 'bandung'},
    }
    import tempfile
    cache = Path(tempfile.gettempdir()) / 'harmonika_research_smoke.sqlite'
    research = Research(cache)
    for collection in COLLECTIONS:
        args = samples[collection]
        try:
            result = research.search(collection, args['query'], limit=args.get('limit', 5),
                                     kind=args.get('kind'), number=args.get('number'),
                                     year=args.get('year'))
            first = result['records'][0]['title'][:70] if result['records'] else None
            print(json.dumps({'collection': collection, 'outcome': result['outcome'],
                              'http_status': result.get('http_status'), 'records': len(result['records']),
                              'portal_url': result['portal']['url'], 'error': result.get('error'),
                              'first_title': first}, ensure_ascii=False))
        except DomainError as e:
            print(json.dumps({'collection': collection, 'outcome': 'raised', 'error': e.code}))
    cache.unlink(missing_ok=True)


if __name__ == '__main__':
    _smoke()
