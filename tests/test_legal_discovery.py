"""Public-discovery research tests. Offline unit suite: no live network.

Fixtures below are synthetic copies of the result structures verified on the
official portals during endpoint research (2026-10-24). Passing these tests is
NOT live proof: live portal status is re-checked by the public_research smoke
runner and reported separately.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.legal_engine import discovery
from backend.legal_engine import public_research as pr
from backend.legal_engine.common import DomainError
from backend.legal_engine.official_network import NetworkError
from backend.legal_engine.registry import IDS

CARIGLOBAL_HTML = '''<html><body>
<div class="strip"><p><a href="/id/pp-no-30-tahun-2026">PERATURAN PEMERINTAH NOMOR 30 TAHUN 2026 TENTANG CONTOH PP</a></p></div>
<div class="strip"><p><a href="/id/perpres-no-71-tahun-2024">PERATURAN PRESIDEN NOMOR 71 TAHUN 2024 TENTANG CONTOH PERPRES</a></p></div>
<div class="strip"><p><a href="/id/permendagri-no-74-tahun-2022">PERATURAN MENTERI DALAM NEGERI NOMOR 74 TAHUN 2022 TENTANG CONTOH DAGRI</a></p></div>
<div class="strip"><p><a href="/id/perda-kabupaten-jombang-no-1-tahun-2026">PERATURAN DAERAH KABUPATEN JOMBANG NOMOR 1 TAHUN 2026 TENTANG CONTOH PERDA</a></p></div>
<div class="strip"><p><a href="/id/permenkes-no-15-tahun-2026">PERATURAN MENTERI KESEHATAN NOMOR 15 TAHUN 2026 TENTANG CONTOH PERMENKES</a></p></div>
</body></html>'''

MK_HTML = '''<html><body><div class="col-lg-8"><div class="strip grid"><div class="wrapper">
<a class="wish_bt" href="/id/#">2024</a>
<p><a href="/putusan/putusan-mahkamah-konstitusi-no-+92-puu-xxii-2024-tahun-2024" title="lihat detail">Pengujian Materiil Undang-Undang Nomor 3 Tahun 2024 tentang Desa</a></p>
<ul><li>Dokumen Putusan : <a href="/files4/putusan-mahkamah-konstitusi-no-+92-puu-xxii-2024-tahun-2024.pdf">pdf</a></li></ul>
</div></div></div></body></html>'''

KOMDIGI_HTML = '''<html><head><style>.card.card-produk-hukum-list{}</style></head><body>
<div class="card card-produk-hukum-list"><div class="card-body">
<a href="https://jdih.komdigi.go.id/produk_hukum/view/id/892/t/peraturan-contoh-nomor-3-tahun-2024?search=panduan">
<div class="row"><div class="col-md-10"><h5 class="produk-hukum-title">Surat Edaran Contoh Nomor 3 Tahun 2024</h5>
<p class="tx-12 mg-b-5 desc text-muted small"> ...panduan <span class="highlight">panduan</span> sirkulasi internal ... </p>
<p class="tx-12 date">12 Mei 2024</p></div></div></a></div></div>
</body></html>'''

ECOLEX_HTML = '''<html><body><ul class="search-results">
<li class="search-result"><h3 class="search-result-title">
<a href="/details/treaty/example-treaty-tre-001147/?q=x&amp;type=treaty"><em class="hl">Example</em> Treaty on Test Waters</a></h3>
<div class="search-result-details"><strong class="sr-type">Treaty</strong> | <span class="sr-doctype">Bilateral</span> | <time class="sr-date" title="Date: Sept. 14, 1993"> Sep 14, 1993 </time></div>
<p class="search-result-details"><strong>Keyword:</strong> <span class="collapse-keywords">Marine fisheries, Test waters</span></p>
</li></ul></body></html>'''

CROSSREF_JSON = json.dumps({'status': 'ok', 'message': {'items': [
    {'title': ['Urgensi Pembaruan Hukum Pidana Indonesia'], 'URL': 'https://doi.org/10.0000/example.1',
     'container-title': ['Jurnal Contoh Hukum'], 'issued': {'date-parts': [[2024]]},
     'abstract': '<jats:p>Ringkasan contoh artikel jurnal hukum.</jats:p>', 'DOI': '10.0000/example.1',
     'type': 'journal-article'}]}})

KY_HTML = '''<html><body><h1>Hasil Pencarian: sanksi</h1>
<a href="/frontend/news_detail/100/terbukti-gratifikasi-hakim-f-diberhentikan-dengan-hormat">Terbukti Gratifikasi, Hakim F Diberhentikan dengan Hormat</a>
<a href="/frontend/announcement_detail/249/pengumuman-kelulusan-seleksi-calon-hakim-agung">Pengumuman Kelulusan Seleksi Calon Hakim Agung Tahun 2026</a>
<a href="/frontend/static_content/member_2015_2020/about_ky">Member Profile 2015 - 2020</a>
</body></html>'''

YLBHI_HTML = '''<html><body>
<ul><li class="elementor-icon-list-item"><a href="https://www.lbhbandung.or.id/"><span class="elementor-icon-list-text">LBH Bandung</span></a></li>
<li class="elementor-icon-list-item"><a href="https://bantuanhukum.or.id/"><span class="elementor-icon-list-text">LBH Jakarta</span></a></li>
</ul></body></html>'''

PERADI_OK_HTML = '''<html><body><table>
<tr><th>Nama</th><th>Nomor Anggota</th></tr>
<tr><td>SITOMPUL, Angus</td><td>A5.12345</td></tr>
<tr><td>SITOMPUL, Angusia</td><td>A5.99999</td></tr>
</table></body></html>'''

PERADI_BROKEN_HTML = '''<html><body> Kompo Not Fundo </body></html>'''


def response_for(body, url='https://fixture.invalid/x', status=200):
    raw = body.encode()
    return {'status': status, 'url': url, 'body_bytes': raw, 'text': raw.decode(),
            'headers': {}, 'content_type': 'text/html'}


class FakeCtx:
    def __init__(self, research):
        self.research = research


class FixtureResearch(pr.Research):
    """Research wired to canned portal responses; never a live-network proof."""

    def __init__(self, cache_path, responses):
        self.responses = responses
        self.calls = []
        super().__init__(cache_path, fetcher_factory=lambda host: SimpleNamespace(
            fetch=lambda url: self._fetch(url)))

    def _fetch(self, url):
        self.calls.append(url)
        for pattern, body in self.responses.items():
            if pattern in url:
                return response_for(body, url)
        raise NetworkError('connect_failed', 'The request could not be completed')


class PortalFetcherTests(unittest.TestCase):
    def test_non_allowlisted_or_plain_http_host_rejected_before_connect(self):
        fetcher = pr.PortalFetcher(frozenset(('peraturan.go.id',)))
        with self.assertRaises(DomainError):
            fetcher._open_connection('93.184.216.34', 443, 'example.com', True, 1)
        with self.assertRaises(DomainError):
            fetcher._open_connection('93.184.216.34', 80, 'peraturan.go.id', False, 1)

    def test_every_collection_maps_to_allowlisted_host(self):
        for collection in pr.COLLECTIONS:
            portal = pr.PORTALS[collection]
            self.assertIn(portal['host'], pr.HOST_ALLOWLIST, collection)

    def test_built_urls_use_fixed_templates_only(self):
        for collection in pr.COLLECTIONS:
            portal = pr.PORTALS[collection]
            params = portal['params']({'query': "x'-- DROP", 'page': 1, 'year': None,
                                       'kind': 'treaty', 'number': None, 'limit': 5})
            url = 'https://' + portal['host'] + ('?' + urlencode(params) if params else '')
            parts = urlsplit(url)
            self.assertEqual(parts.hostname, portal['host'])
            self.assertEqual(parts.scheme, 'https')
            self.assertIsNone(parts.port)


class ResearchParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / 'research.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def wired(self, responses):
        return FixtureResearch(self.cache, responses)

    def test_regulation_slug_filter_scopes_collections(self):
        research = self.wired({'peraturan.go.id': CARIGLOBAL_HTML})
        slugs = {}
        for collection in ('kb.pp.search', 'kb.perpres.search', 'kb.permendagri.search', 'kb.perda.search'):
            result = research.search(collection, 'contoh', limit=10)
            self.assertEqual(result['outcome'], 'match', collection)
            slugs[collection] = [r['regulation_slug'] for r in result['records']]
        self.assertEqual([s.split('-no-')[0] for s in slugs['kb.pp.search']], ['pp'])
        self.assertTrue(all(s.startswith('perpres-') for s in slugs['kb.perpres.search']))
        self.assertTrue(all(s.startswith('permendagri-') for s in slugs['kb.permendagri.search']))
        self.assertTrue(all(s.startswith('perda-') for s in slugs['kb.perda.search']))
        result = research.search('kb.pp.search', 'contoh', limit=10, year=2026)
        self.assertTrue(all(r['year'] == 2026 for r in result['records']))
        result = research.search('kb.pp.search', 'contoh', limit=10, year=1999)
        self.assertEqual(result['outcome'], 'no_match')

    def test_putusan_mk_parses_document_link_and_year(self):
        research = self.wired({'peraturan.go.id': MK_HTML})
        result = research.search('kb.putusan.mk.search', '', number='92', year=2024)
        self.assertEqual(result['outcome'], 'match')
        record = result['records'][0]
        self.assertTrue(record['source_url'].startswith('https://peraturan.go.id/putusan/'))
        self.assertTrue(record['document_url'].endswith('.pdf'))
        self.assertEqual(record['year'], 2024)

    def test_sirclo_parses_internal_circulation_records(self):
        research = self.wired({'jdih.komdigi.go.id': KOMDIGI_HTML})
        result = research.search('kb.sirclo.search', 'panduan')
        self.assertEqual(result['outcome'], 'match')
        record = result['records'][0]
        self.assertEqual(record['circulation_category'], 'surat_edaran_jdih_internal')
        self.assertIn('panduan', record['excerpt'])

    def test_ecolex_treaty_and_court_decision(self):
        research = self.wired({'www.ecolex.org': ECOLEX_HTML})
        result = research.search('kb.intl.law.search', 'test', kind='treaty')
        self.assertEqual(result['records'][0]['document_kind'], 'treaty')
        self.assertEqual(result['records'][0]['entry_type'], 'Treaty')
        result = research.search('kb.intl.case.search', 'test', kind='court_decision')
        self.assertEqual(result['outcome'], 'match')

    def test_crossref_articles_with_clean_abstract(self):
        research = self.wired({'api.crossref.org': CROSSREF_JSON})
        result = research.search('kb.article.legal.search', 'hukum pidana')
        record = result['records'][0]
        self.assertEqual(record['doi'], '10.0000/example.1')
        self.assertEqual(record['year'], 2024)
        self.assertNotIn('<jats:', record['excerpt'])

    def test_ky_kinds_and_lbh_directory_query_match(self):
        research = self.wired({'www.komisiyudisial.go.id': KY_HTML, 'ylbhi.or.id': YLBHI_HTML})
        result = research.search('kb.konsil.yudisial', 'sanksi hakim')
        kinds = {r['ky_content_kind'] for r in result['records']}
        self.assertIn('news', kinds)
        self.assertIn('announcement', kinds)
        result = research.search('kb.lbh.search', 'bandung')
        self.assertEqual(result['outcome'], 'match')
        self.assertEqual(result['records'][0]['title'], 'LBH Bandung')
        result = research.search('kb.lbh.search', 'krypton')
        self.assertEqual(result['outcome'], 'no_match')

    def test_regulation_identifier_lookup_uses_typed_server_side_page(self):
        research = self.wired({'peraturan.go.id': CARIGLOBAL_HTML})
        result = research.search('kb.pp.search', '', number='30', year=2026)
        self.assertTrue(research.calls[0].startswith('https://peraturan.go.id/pp?'))
        self.assertIn('PeraturanSearch%5Bnomor%5D=30', research.calls[0])
        self.assertEqual(result['outcome'], 'match')
        research.search('kb.perpres.search', '', number='71', year=2024)
        self.assertIn('/perpres?', research.calls[1])
        research.search('kb.permendagri.search', '', number='74', year=2022)
        self.assertIn('/permen?', research.calls[2])
        research.search('kb.perda.search', '', number='1', year=2026)
        self.assertIn('/perda?', research.calls[3])
        with self.assertRaises(DomainError):
            research.search('kb.pp.search', '')

    def test_error_outcome_is_distinct_from_no_match(self):
        research = self.wired({'peraturan.go.id': CARIGLOBAL_HTML})
        result = research.search('kb.putusan.ma.search', 'kecelakaan')
        self.assertEqual(result['outcome'], 'error')
        self.assertEqual(result['error']['code'], 'connect_failed')
        self.assertEqual(result['records'], [])

    def test_unrecognized_structure_yields_error_not_fake_no_match(self):
        research = self.wired({'peradi.or.id': PERADI_BROKEN_HTML})
        result = research.search('kb.advokat.verify', 'A5.12345')
        self.assertEqual(result['outcome'], 'error')
        self.assertEqual(result['error']['code'], 'portal_search_component_unavailable')

    def test_cache_hit_skips_network_and_never_persists_query_text(self):
        research = self.wired({'peraturan.go.id': CARIGLOBAL_HTML})
        research.search('kb.pp.search', 'RAHASIA-KLIEN-123')
        research.search('kb.pp.search', 'RAHASIA-KLIEN-123')
        self.assertEqual(len(research.calls), 1)
        import sqlite3
        with sqlite3.connect(research.cache_path) as db:
            rows = db.execute('SELECT key, collection, payload FROM research_cache').fetchall()
            index_rows = db.execute('SELECT title, excerpt, source_url FROM index_documents').fetchall()
        blob = json.dumps(rows) + json.dumps(index_rows)
        self.assertNotIn('RAHASIA-KLIEN-123', blob)
        self.assertTrue(index_rows)

    def test_expired_cache_refetches(self):
        research = self.wired({'peraturan.go.id': CARIGLOBAL_HTML})
        research.search('kb.pp.search', 'contoh', ttl_seconds=1)
        import time as time_module
        with patch.object(pr.time, 'time', return_value=pr.time.time() + 10):
            research.search('kb.pp.search', 'contoh', ttl_seconds=1)
        self.assertEqual(len(research.calls), 2)

    def test_advokat_rows_are_never_persisted(self):
        research = self.wired({'peradi.or.id': PERADI_OK_HTML})
        research.search('kb.advokat.verify', 'A5.12345')
        import sqlite3
        with sqlite3.connect(research.cache_path) as db:
            cache_rows = db.execute('SELECT count(*) FROM research_cache').fetchone()[0]
            index_rows = db.execute('SELECT count(*) FROM index_documents').fetchone()[0]
        self.assertEqual((cache_rows, index_rows), (0, 0))

    def test_input_bounds(self):
        research = self.wired({})
        with self.assertRaises(DomainError):
            research.search('kb.pp.search', '')
        with self.assertRaises(DomainError):
            research.search('kb.pp.search', 'x' * 501)
        with self.assertRaises(DomainError):
            research.search('kb.pp.search', 'x', limit=11)
        with self.assertRaises(DomainError):
            research.search('kb.pp.search', 'x', page=9)
        with self.assertRaises(DomainError):
            research.search('kb.unknown.search', 'x')


class DiscoveryHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / 'research.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def wired(self, responses):
        return FixtureResearch(self.cache, responses)

    def test_module_contract_matches_registry(self):
        self.assertEqual(set(discovery.HANDLERS), set(discovery.SCHEMAS))
        self.assertLessEqual(set(discovery.HANDLERS), IDS)
        self.assertEqual(set(discovery.HANDLERS), set(pr.COLLECTIONS))
        self.assertEqual(discovery.MUTATIONS, set())
        self.assertEqual(set(discovery.LIMITATIONS), set(discovery.HANDLERS))

    def test_missing_research_adapter_fails_closed(self):
        with self.assertRaises(DomainError) as caught:
            discovery.HANDLERS['kb.pp.search'](FakeCtx(None), {'query': 'contoh'})
        self.assertEqual(caught.exception.code, 'research_adapter_required')
        with self.assertRaises(DomainError):
            discovery.HANDLERS['kb.pp.search'](SimpleNamespace(), {'query': 'contoh'})

    def test_handler_envelope_honesty_fields(self):
        research = self.wired({'peraturan.go.id': CARIGLOBAL_HTML})
        out = discovery.HANDLERS['kb.pp.search'](FakeCtx(research), {'query': 'contoh'})
        self.assertEqual(out['collection'], 'kb.pp.search')
        self.assertEqual(out['outcome'], 'match')
        self.assertTrue(out['research_snippet_not_statutory_quote'])
        self.assertTrue(out['match_is_not_current_law_certification'])
        self.assertFalse(out['current_law_certified'])
        self.assertEqual(out['status'], 'completed')
        self.assertEqual(out['query_scope']['query'], 'contoh')
        self.assertNotIn('RAHASIA', json.dumps(out))
        validate = out['records'][0]
        for field in ('source_url', 'title', 'excerpt', 'sha256', 'fetched_at'):
            self.assertIn(field, validate)

    def test_handler_error_status_is_degraded_and_distinct(self):
        research = self.wired({})
        out = discovery.HANDLERS['kb.putusan.ma.search'](FakeCtx(research), {'query': 'x'})
        self.assertEqual(out['status'], 'degraded')
        self.assertEqual(out['outcome'], 'error')
        self.assertIn('error', out)
        out = discovery.HANDLERS['kb.putusan.pengadilan.search'](FakeCtx(research), {'query': 'x'})
        self.assertEqual(out['status'], 'degraded')

    def test_mk_requires_some_scope(self):
        research = self.wired({'peraturan.go.id': MK_HTML})
        with self.assertRaises(DomainError):
            discovery.HANDLERS['kb.putusan.mk.search'](FakeCtx(research), {'query': ''})
        out = discovery.HANDLERS['kb.putusan.mk.search'](FakeCtx(research),
                                                         {'query': '', 'number': '92', 'year': 2024})
        self.assertEqual(out['outcome'], 'match')

    def test_advokat_exact_match_never_partial_and_never_certifies(self):
        research = self.wired({'peradi.or.id': PERADI_OK_HTML})
        out = discovery.HANDLERS['kb.advokat.verify'](FakeCtx(research), {'identifier': 'A5.12345'})
        self.assertEqual(out['outcome'], 'verified_in_public_directory')
        self.assertTrue(out['exact_identifier_match'])
        self.assertEqual(out['matched_record']['identifier_fields'][1], 'A5.12345')
        out = discovery.HANDLERS['kb.advokat.verify'](FakeCtx(research), {'identifier': 'A5.1234'})
        self.assertEqual(out['outcome'], 'no_exact_match')
        self.assertFalse(out['exact_identifier_match'])
        self.assertIsNone(out['matched_record'])
        self.assertIsNone(out['misconduct_or_disciplinary_determination'])
        self.assertFalse(out['identity_certified'])
        self.assertFalse(out['license_or_good_standing_certified'])
        self.assertFalse(out['registry_invented'])

    def test_advokat_broken_portal_is_error_not_invented_registry(self):
        research = self.wired({'peradi.or.id': PERADI_BROKEN_HTML})
        out = discovery.HANDLERS['kb.advokat.verify'](FakeCtx(research), {'identifier': 'A5.12345'})
        self.assertEqual(out['outcome'], 'error')
        self.assertEqual(out['status'], 'degraded')
        self.assertIsNone(out['exact_identifier_match'])
        self.assertFalse(out['registry_invented'])
        self.assertIn('tidak ada pernyataan keanggotaan', out['note'])

    def test_lbh_has_no_distance_field(self):
        research = self.wired({'ylbhi.or.id': YLBHI_HTML})
        out = discovery.HANDLERS['kb.lbh.search'](FakeCtx(research), {'query': 'bandung'})
        self.assertTrue(out['published_directory_only'])
        self.assertFalse(out['distance_or_proximity_estimated'])
        self.assertNotIn('distance_km', json.dumps(out))

    def test_sirclo_collection_is_jdih_circulation_not_company(self):
        research = self.wired({'jdih.komdigi.go.id': KOMDIGI_HTML})
        out = discovery.HANDLERS['kb.sirclo.search'](FakeCtx(research), {'query': 'panduan'})
        self.assertEqual(out['portal']['host'], 'jdih.komdigi.go.id')
        self.assertIn('Sirkulasi internal', out['portal']['name'])

    def test_all_handlers_validate_input_schema(self):
        from backend.legal_engine.common import validate
        good = {
            'kb.putusan.ma.search': {'query': 'x'},
            'kb.putusan.mk.search': {'query': 'x'},
            'kb.putusan.pengadilan.search': {'query': 'x'},
            'kb.pp.search': {'query': 'x', 'year': 2024},
            'kb.perpres.search': {'query': 'x'},
            'kb.permendagri.search': {'query': 'x'},
            'kb.perda.search': {'query': 'x'},
            'kb.sirclo.search': {'query': 'x'},
            'kb.intl.law.search': {'query': 'x', 'kind': 'treaty'},
            'kb.intl.case.search': {'query': 'x', 'kind': 'court_decision'},
            'kb.article.legal.search': {'query': 'x'},
            'kb.konsil.yudisial': {'query': 'x'},
            'kb.advokat.verify': {'identifier': 'A5.12345'},
            'kb.lbh.search': {'query': 'x'},
        }
        for skill_id, args in good.items():
            validate(args, discovery.SCHEMAS[skill_id])
        bad = [
            ('kb.putusan.ma.search', {'query': 'x', 'extra': 1}),
            ('kb.intl.law.search', {'query': 'x', 'kind': 'company_law'}),
            ('kb.advokat.verify', {'identifier': 'ab'}),
            ('kb.advokat.verify', {'query': 'x'}),
            ('kb.pp.search', {'query': 'x', 'year': 1800}),
        ]
        for skill_id, args in bad:
            with self.assertRaises(DomainError, msg=skill_id):
                validate(args, discovery.SCHEMAS[skill_id])


if __name__ == '__main__':
    unittest.main()
