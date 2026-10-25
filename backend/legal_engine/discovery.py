"""Scoped public-discovery handlers (Layer 3 KB) over fixed official indexes.

All handlers are read-only research lookups on fixed official portals. A fetched
public record proves where a document was published, never what the current law
is, whether a person holds a valid license, or who committed misconduct.
Research excerpts are portal snippets; they are NOT statutory quotes — quotable
law text lives only in the trusted ingested Sources corpus (see sources.py).

Handlers require the root-injected adapter at ctx.research (public_research.
Research); without it they fail closed with research_adapter_required.
"""
from .common import DomainError, require
from .public_research import PORTALS

QUERY = {'type': 'string', 'minLength': 1, 'maxLength': 500}
IDENTIFIER = {'type': 'string', 'minLength': 3, 'maxLength': 100}
LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 10}
PAGE = {'type': 'integer', 'minimum': 1, 'maximum': 5}
YEAR = {'type': 'integer', 'minimum': 1950, 'maximum': 2106}
LAW_KIND = {'type': 'string', 'enum': ['treaty', 'legislation']}
CASE_KIND = {'type': 'string', 'enum': ['court_decision']}
MK_NUMBER = {'type': 'string', 'minLength': 1, 'maxLength': 30}
HANDLERS = {}
SCHEMAS = {}
MUTATIONS = set()
LIMITATIONS = {}


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties,
            'required': list(properties) if required is None else required,
            'additionalProperties': False}


def reg(skill_id, schema, fn, note):
    HANDLERS[skill_id] = fn
    SCHEMAS[skill_id] = schema
    LIMITATIONS[skill_id] = note


def _adapter(c):
    research = getattr(c, 'research', None)
    require(research is not None, 'research_adapter_required', 503)
    return research


def _envelope(collection, scope, result, query=None):
    portal_meta = PORTALS[collection]
    out = {
        'collection': collection,
        'query_scope': {'query_sha256': result['query_sha256'],
                        'portal_url': result['portal']['url'],
                        **{k: v for k, v in scope.items() if v is not None}},
        'portal': {'name': result['portal'].get('name',portal_meta['name']), 'host': result['portal'].get('host',portal_meta['host']),
                   'url': result['portal']['url'], 'official': True,'fallback':result['portal'].get('fallback',False)},
        'index_form': result['portal'].get('index_form',portal_meta['index_form']),
        'coverage': result['portal'].get('coverage',portal_meta['coverage']),
        'outcome': result['outcome'],
        'records': result['records'],
        'record_count': len(result['records']),
        'records_seen_before_filter': result.get('records_seen_before_filter', 0),
        'research_snippet_not_statutory_quote': True,
        'match_is_not_current_law_certification': True,
        'current_law_certified': False,
        'fetched_at': result['fetched_at'],
        'raw_response_sha256': result.get('raw_sha256'),
        'cache': result.get('cache', 'miss'),
        'status': 'completed',
    }
    if query is not None:
        out['query_scope']['query'] = query
    if result['outcome'] == 'error':
        out['status'] = 'degraded'
        out['error'] = result.get('error', {'code': 'research_error'})
    return out


def _simple(collection, scope_builder):
    def handler(c, a):
        research = _adapter(c)
        scope = scope_builder(a)
        result = research.search(collection, scope.pop('query'), **scope)
        return _envelope(collection, scope, result, query=a.get('query'))
    return handler


def _mk_scope(a):
    require(a.get('query') or a.get('number') or a.get('year'), 'empty_research_scope')
    return {'query': a.get('query', ''), 'number': a.get('number'),
            'year': a.get('year'), 'page': a.get('page', 1)}


def _mk_handler(c, a):
    research = _adapter(c)
    scope = _mk_scope(a)
    result = research.search('kb.putusan.mk.search', scope['query'], number=scope['number'],
                             year=scope['year'], page=scope['page'])
    return _envelope('kb.putusan.mk.search', scope, result,
                     query=a.get('query'))


def _advokat_handler(c, a):
    research = _adapter(c)
    identifier = ' '.join(a['identifier'].split())
    result = research.search('kb.advokat.verify', a['identifier'])
    base = {
        'collection': 'kb.advokat.verify',
        'portal': {'name': PORTALS['kb.advokat.verify']['name'],
                   'host': PORTALS['kb.advokat.verify']['host'], 'official': True},
        'index_form': PORTALS['kb.advokat.verify']['index_form'],
        'coverage': PORTALS['kb.advokat.verify']['coverage'],
        'identifier_provided_sha256': result['query_sha256'],
        'research_snippet_not_statutory_quote': True,
        'registry_invented': False,
        'cache': result.get('cache', 'miss'),
    }
    if result['outcome'] == 'error':
        base.update({'outcome': 'error', 'status': 'degraded',
                     'error': result.get('error', {'code': 'research_error'}),
                     'exact_identifier_match': None, 'matched_record': None,
                     'directory_rows_seen': 0,
                     'identity_certified': False,
                     'license_or_good_standing_certified': False,
                     'misconduct_or_disciplinary_determination': None,
                     'note': 'Direktori resmi tidak dapat diakses saat ini; tidak ada pernyataan keanggotaan.'})
        return base
    folded = identifier.casefold()
    matched = None
    for record in result['records']:
        for field in record.get('identifier_fields', ()):
            if ' '.join(str(field).split()).casefold() == folded:
                matched = record
                break
        if matched:
            break
    base.update({'outcome': 'verified_in_public_directory' if matched else 'no_exact_match',
                 'status': 'completed',
                 'exact_identifier_match': matched is not None,
                 'matched_record': matched,
                 'directory_rows_seen': result.get('records_seen_before_filter', 0),
                 'identity_certified': False,
                 'identity_of_requester_verified': False,
                 'license_or_good_standing_certified': False,
                 'misconduct_or_disciplinary_determination': None})
    return base


def _lbh_handler(c, a):
    research = _adapter(c)
    result = research.search('kb.lbh.search', a['query'], limit=a.get('limit', 5))
    out = _envelope('kb.lbh.search', {}, result, query=a.get('query'))
    out['published_directory_only'] = True
    out['distance_or_proximity_estimated'] = False
    return out


def _law_scope(a):
    return {'query': a['query'], 'kind': a.get('kind'), 'limit': a.get('limit', 5)}


def _reg_scope(a):
    require(a.get('query') or a.get('number') or a.get('year'),'empty_research_scope')
    return {'query': a.get('query',''), 'year': a.get('year'), 'page': a.get('page', 1),
            'number': a.get('number')}


reg('kb.putusan.ma.search', obj({'query': QUERY, 'limit': LIMIT, 'page': PAGE}, ['query']),
    _simple('kb.putusan.ma.search', lambda a: {'query': a['query'], 'limit': a.get('limit', 5), 'page': a.get('page', 1)}),
    'Pencarian terbatas direktori putusan MA pada portal resmi; portal saat ini menolak akses '
    'sisi-server (diverifikasi 2026-10-24) sehingga hasil berstatus error, bukan kosong-palsu. '
    'Kutipan riset bukan kutipan pasal dan bukan sertifikasi hukum yang berlaku.')
reg('kb.putusan.mk.search', obj({'query': {**QUERY,'minLength':0}, 'number': MK_NUMBER, 'year': YEAR, 'page': PAGE},
                                []),
    _mk_handler,
    'Hanya putusan MK yang diterbitkan pada portal peraturan.go.id; bukan seluruh arsip MK dan '
    'bukan status berlaku suatu norma. Kutipan riset bukan kutipan pasal.')
reg('kb.putusan.pengadilan.search', obj({'query': QUERY, 'limit': LIMIT, 'page': PAGE}, ['query']),
    _simple('kb.putusan.pengadilan.search', lambda a: {'query': a['query'], 'limit': a.get('limit', 5), 'page': a.get('page', 1)}),
    'Pencarian terbatas direktori putusan pengadilan pada portal resmi MA; portal saat ini menolak '
    'akses sisi-server (diverifikasi 2026-10-24). Hasil kosong resmi berbeda dari error akses. '
    'Kutipan riset bukan kutipan pasal.')
reg('kb.pp.search', obj({'query': {**QUERY,'minLength':0}, 'number': MK_NUMBER, 'year': YEAR, 'page': PAGE}, []),
    _simple('kb.pp.search', _reg_scope),
    'Nomor/tahun: pencarian terbatas PP sisi-server di peraturan.go.id. Kata kunci: indeks global '
    'resmi difilter ke slug PP, sehingga no_match di halaman awal bukan bukti ketiadaan PP. '
    'Bukan status berlaku. Kutipan riset bukan kutipan pasal.')
reg('kb.perpres.search', obj({'query': {**QUERY,'minLength':0}, 'number': MK_NUMBER, 'year': YEAR, 'page': PAGE}, []),
    _simple('kb.perpres.search', _reg_scope),
    'Nomor/tahun: pencarian terbatas Perpres sisi-server. Kata kunci: indeks global resmi '
    'difilter ke slug Perpres. Bukan status berlaku. Kutipan riset bukan kutipan pasal.')
reg('kb.permendagri.search', obj({'query': {**QUERY,'minLength':0}, 'number': MK_NUMBER, 'year': YEAR, 'page': PAGE}, []),
    _simple('kb.permendagri.search', _reg_scope),
    'Nomor/tahun: pencarian peraturan menteri sisi-server lalu difilter ke slug Permendagri; '
    'portal JDIH Kemendagiri langsung tidak tersedia dari jaringan ini (NXDOMAIN). Kata kunci: '
    'indeks global difilter ke slug Permendagri. Kutipan riset bukan kutipan pasal.')
reg('kb.perda.search', obj({'query': {**QUERY,'minLength':0}, 'number': MK_NUMBER, 'year': YEAR, 'page': PAGE}, []),
    _simple('kb.perda.search', _reg_scope),
    'Nomor/tahun: pencarian Perda sisi-server; daerah berbeda dengan nomor sama semuanya tampil. '
    'Kata kunci: indeks global difilter ke slug Perda dan jauh dari lengkap seluruh daerah. '
    'Kutipan riset bukan kutipan pasal.')
reg('kb.sirclo.search', obj({'query': QUERY, 'year': YEAR}, ['query']),
    _simple('kb.sirclo.search', lambda a: {'query': a['query'], 'year': a.get('year')}),
    'Sirkulasi internal JDIH Komdigi (kategori Surat Edaran) saja; bukan perusahaan SIRCLO dan '
    'bukan seluruh JDIH pemerintah. Kutipan riset bukan kutipan pasal.')
reg('kb.intl.law.search', obj({'query': QUERY, 'kind': LAW_KIND, 'limit': LIMIT}, ['query']),
    _simple('kb.intl.law.search', _law_scope),
    'Hanya treaty/legislasi terindeks ECOLEX (FAO/IUCN/UNEP); bukan seluruh hukum internasional '
    'dan bukan penentuan hukum mana yang berlaku untuk kasus. Kutipan riset bukan kutipan pasal.')
reg('kb.intl.case.search', obj({'query': QUERY, 'kind': CASE_KIND, 'limit': LIMIT}, ['query']),
    _simple('kb.intl.case.search', _law_scope),
    'Hanya putusan pengadilan terindeks ECOLEX; portal ICJ/ICC/ECtHR menolak akses sisi-server. '
    'Bukan seluruh yurisdiksi internasional. Kutipan riset bukan kutipan pasal.')
reg('kb.article.legal.search', obj({'query': QUERY, 'year': YEAR, 'limit': LIMIT}, ['query']),
    _simple('kb.article.legal.search',
            lambda a: {'query': a['query'], 'year': a.get('year'), 'limit': a.get('limit', 5)}),
    'Metadata artikel ber-DOI dari Crossref; bukan jurnal seluruh dunia, bukan endorsasi isi '
    'artikel, dan bukan sumber hukum. Kutipan riset bukan kutipan pasal.')
reg('kb.konsil.yudisial', obj({'query': QUERY, 'limit': LIMIT}, ['query']),
    _simple('kb.konsil.yudisial', lambda a: {'query': a['query'], 'limit': a.get('limit', 5)}),
    'Hanya konten terbit KY (berita, pengumuman, rilis pers, profil anggota); bukan putusan '
    'atas laporan tertentu untuk identitas penuh dan bukan penentuan pelanggaran. '
    'Kutipan riset bukan kutipan pasal.')
reg('kb.advokat.verify', obj({'identifier': IDENTIFIER}, ['identifier']),
    _advokat_handler,
    'Pencocokan identifier eksak pada direktori terbit saja; TIDAK mensertifikasi identitas, '
    'status anggota, kecocokan orang, atau perilaku; tidak pernah membuat registry sendiri. '
    'Komponen pencarian PERADI saat ini tidak tersedia (diverifikasi 2026-10-24) sehingga '
    'hasil berstatus error sampai portal pulih.')
reg('kb.lbh.search', obj({'query': QUERY, 'limit': LIMIT}, ['query']),
    _lbh_handler,
    'Hanya direktori kantor LBH yang diterbitkan YLBHI; tanpa estimasi jarak, tanpa klaim '
    'layanan aktif, dan bukan seluruh LBH di Indonesia. Kutipan riset bukan kutipan pasal.')
