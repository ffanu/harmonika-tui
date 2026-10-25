"""Fallback-research tests. Offline unit suite: no live network.

Fixtures are synthetic copies of the listing structure verified on the
official JDIH Kemenkes portal (live 2026-10-24). Passing these tests is NOT
live proof: portal status is re-checked by the live smoke and reported
separately, including the hosts that are blocked (403) and the advocate
alternative that is not used because its ownership provenance is unverified.
"""
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.legal_engine import public_research as pr
from backend.legal_engine import research_fallbacks as rf
from backend.legal_engine.common import DomainError
from backend.legal_engine.official_network import NetworkError
from backend.legal_engine.public_research import PortalStructureError

KEMKES_PAGE1 = '''<html><body><main>
<a href="https://jdih.kemkes.go.id/putusan/putusan-mahkamah-agung-nomor-78-ktun2026-dengan-penggugat-contoh-satu">Putusan Mahkamah Agung Nomor 78 K/TUN/2026 dengan Penggugat Contoh Satu tentang Wanprestasi Kontrak</a>
<a href="https://jdih.kemkes.go.id/putusan/putusan-mahkamah-agung-nomor-78-ktun2026-dengan-penggugat-contoh-satu">Detail</a>
<a href="https://jdih.kemkes.go.id/putusan/putusan-pengadilan-negeri-bandung-nomor-544pdtg2025pn-bdg">Putusan Pengadilan Negeri Bandung Nomor 544/Pdt.G/2025/PN Bdg</a>
<a href="https://jdih.kemkes.go.id/putusan/putusan-pengadilan-negeri-bandung-nomor-544pdtg2025pn-bdg">Detail</a>
<a href="https://jdih.kemkes.go.id/putusan/putusan-mahkamah-konstitusi-nomor-111puu-xxii2024">Putusan Mahkamah Konstitusi Nomor 111/PUU-XXII/2024</a>
<a href="https://jdih.kemkes.go.id/putusan/putusan-mahkamah-konstitusi-nomor-111puu-xxii2024">Detail</a>
<a href="https://jdih.kemkes.go.id/putusan/putusan-mahkamah-agung-nomor-123-kpdt2026-truncated-">Putusan Mahkamah Agung Nomor 123 K/Pdt/2026 dengan Penggugat Contoh Dua</a>
</main></body></html>'''

KEMKES_PAGE2 = '''<html><body><main>
<a href="https://jdih.kemkes.go.id/putusan/putusan-pengadilan-tinggi-dki-jakarta-nomor-82pdt2026pt-dki">Putusan Pengadilan Tinggi DKI Jakarta Nomor 82/Pdt/2026/PT DKI</a>
<a href="https://jdih.kemkes.go.id/putusan/putusan-pengadilan-tinggi-dki-jakarta-nomor-82pdt2026pt-dki">Detail</a>
</main></body></html>'''

KEMKES_EMPTY = '<html><body><main><p>Belum ada dokumen.</p></main></body></html>'

PUTUSAN3_OK = '''<html><body>
<a href="/direktori/putusan/abc123/putusan-mahkamah-agung-nomor-77-k-tahun-2026">Putusan Mahkamah Agung Nomor 77 K/2026 Tentang Contoh Sengketa</a>
</body></html>'''

MK_OK = '''<html><body><div class="strip"><a href="/putusan/putusan-mahkamah-konstitusi-no-92-puu-xxii-2024-tahun-2024">Pengujian Materiil Undang-Undang Nomor 3 Tahun 2024 tentang Contoh</a></div></body></html>'''

PERADI_BROKEN = '<html><body> Kompo Not Fundo </body></html>'

PRIMARY_RESULT_KEYS = {'query_sha256', 'outcome', 'records', 'portal', 'fetched_at',
                       'raw_sha256', 'http_status', 'records_seen_before_filter', 'cache'}


def response_for(body, url):
    raw = body.encode()
    return {'status': 200, 'url': url, 'body_bytes': raw, 'text': raw.decode(),
            'headers': {}, 'content_type': 'text/html'}


class FakeFetcher:
    def __init__(self, behavior):
        self.behavior = behavior
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return self.behavior(url)


class FixtureResearch(rf.FallbackResearch):
    """FallbackResearch wired to canned responses; never a live-network proof."""

    def __init__(self, cache_path, behavior):
        self.fetcher = FakeFetcher(behavior)
        super().__init__(cache_path, fetcher_factory=lambda host: self.fetcher)


def kemkes_behavior(mapping=None, default_page1=KEMKES_PAGE1):
    mapping = mapping or {}

    def behavior(url):
        if 'jdih.kemkes.go.id' in url:
            if 'page=2' in url:
                return response_for(mapping.get('page2', KEMKES_PAGE2), url)
            return response_for(mapping.get('page1', default_page1), url)
        raise NetworkError('connect_failed', 'The request could not be completed')

    return behavior


class FallbackRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(pr, 'PORTAL_GAP_SECONDS', 0.0)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def research(self, behavior, name='fallback.sqlite'):
        return FixtureResearch(Path(self.tmp.name) / name, behavior)

    def test_primary_error_falls_back_and_matches(self):
        r = self.research(kemkes_behavior())
        result = r.search('kb.putusan.ma.search', 'wanprestasi kontrak')
        self.assertEqual(result['outcome'], 'match')
        self.assertEqual(result['portal']['host'], 'jdih.kemkes.go.id')
        self.assertTrue(result['portal']['fallback'])
        self.assertTrue(result['portal']['official'])
        self.assertIn('coverage', result['portal'])
        record = result['records'][0]
        self.assertEqual(record['title'],
                         'Putusan Mahkamah Agung Nomor 78 K/TUN/2026 '
                         'dengan Penggugat Contoh Satu tentang Wanprestasi Kontrak')
        body = json.dumps({'source_url': record['source_url'], 'title': record['title'],
                           'excerpt': record['excerpt']},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.assertEqual(record['sha256'], hashlib.sha256(body.encode()).hexdigest())
        self.assertTrue(record['source_url'].startswith('https://jdih.kemkes.go.id/putusan/'))
        self.assertTrue(record['fetched_at'])

    def test_envelope_reports_listing_matching_instead_of_server_search(self):
        from backend.legal_engine.discovery import _envelope
        result = self.research(kemkes_behavior()).search('kb.putusan.ma.search','wanprestasi')
        out = _envelope('kb.putusan.ma.search',{},result)
        self.assertEqual(out['index_form'],'official_published_listing_client_side_matched')
        self.assertTrue(out['portal']['fallback'])

    def test_result_shape_matches_primary_keys(self):
        r = self.research(kemkes_behavior())
        result = r.search('kb.putusan.ma.search', 'wanprestasi')
        self.assertEqual(set(result), PRIMARY_RESULT_KEYS)

    def test_court_kind_filtering_per_collection(self):
        r = self.research(kemkes_behavior())
        ma = r.search('kb.putusan.ma.search', 'bandung', limit=1)
        self.assertEqual(ma['outcome'], 'no_match')
        pengadilan_negeri = r.search('kb.putusan.pengadilan.search', 'bandung', limit=1)
        self.assertEqual(pengadilan_negeri['outcome'], 'match')
        self.assertEqual(pengadilan_negeri['records'][0]['court_kind'], 'pengadilan_negeri')
        self.assertEqual(pengadilan_negeri['portal']['pages_fetched'], 1)
        pengadilan_tinggi = r.search('kb.putusan.pengadilan.search', 'jakarta', limit=1)
        self.assertEqual(pengadilan_tinggi['outcome'], 'match')
        self.assertEqual(pengadilan_tinggi['records'][0]['court_kind'], 'pengadilan_tinggi')
        self.assertEqual(pengadilan_tinggi['portal']['pages_fetched'], 2)

    def test_no_match_distinct_from_error(self):
        r = self.research(kemkes_behavior())
        no_match = r.search('kb.putusan.ma.search', 'zzz tidak ada')
        self.assertEqual(no_match['outcome'], 'no_match')
        self.assertEqual(no_match['records'], [])
        self.assertTrue(no_match['portal']['fallback'])
        error = r.search('kb.advokat.verify', 'A.123456')
        self.assertEqual(error['outcome'], 'error')
        self.assertNotEqual(no_match['outcome'], error['outcome'])

    def test_fallback_unreachable_returns_primary_error_unchanged(self):
        r = self.research(putusan3_down)
        result = r.search('kb.putusan.ma.search', 'wanprestasi')
        self.assertEqual(result['outcome'], 'error')
        self.assertIn('putusan3.mahkamahagung.go.id', result['portal']['url'])
        self.assertIn('error', result)
        self.assertFalse(result['portal'].get('fallback', False))

    def test_primary_success_is_never_shadowed(self):
        r = self.research(lambda url: response_for(PUTUSAN3_OK, url)
                          if 'putusan3' in url
                          else (_ for _ in ()).throw(NetworkError('connect_failed', 'x')))
        result = r.search('kb.putusan.ma.search', 'sengketa')
        self.assertEqual(result['outcome'], 'match')
        self.assertFalse(result['portal'].get('fallback', False))
        kemkes_calls = [u for u in r.fetcher.urls if 'kemkes' in u]
        self.assertEqual(kemkes_calls, [])

    def test_working_primary_collection_ignores_fallback(self):
        r = self.research(lambda url: response_for(MK_OK, url), name='mk.sqlite')
        result = r.search('kb.putusan.mk.search', 'contoh')
        self.assertEqual(result['outcome'], 'match')
        self.assertFalse(result['portal'].get('fallback', False))

    def test_cache_hit_skips_fallback_network_and_keeps_provenance(self):
        r = self.research(kemkes_behavior())
        first = r.search('kb.putusan.ma.search', 'wanprestasi kontrak')
        kemkes_after_first = [u for u in r.fetcher.urls if 'kemkes' in u]
        second = r.search('kb.putusan.ma.search', 'wanprestasi kontrak')
        self.assertEqual(second['cache'], 'hit')
        self.assertEqual(first['cache'], 'miss')
        self.assertEqual(second['records'], first['records'])
        self.assertEqual(second['portal']['host'], 'jdih.kemkes.go.id')
        self.assertTrue(second['portal']['fallback'])
        kemkes_after_second = [u for u in r.fetcher.urls if 'kemkes' in u]
        self.assertEqual(kemkes_after_second, kemkes_after_first)

    def test_cache_never_stores_query_text(self):
        r = self.research(kemkes_behavior())
        r.search('kb.putusan.ma.search', 'rahasia-klien-xyz')
        raw = (Path(self.tmp.name) / 'fallback.sqlite').read_bytes()
        self.assertNotIn(b'rahasia-klien-xyz', raw)

    def test_page_walk_bounded_when_nothing_matches(self):
        def fresh_nonmatching_page(url):
            if 'jdih.kemkes.go.id' not in url:
                raise NetworkError('connect_failed', 'The request could not be completed')
            page = '1'
            marker = re.search(r'page=(\d+)', url)
            if marker:
                page = marker.group(1)
            row = ('<a href="https://jdih.kemkes.go.id/putusan/putusan-mahkamah-konstitusi-'
                   'nomor-' + page + 'puu-xxii2024">Putusan Mahkamah Konstitusi Nomor '
                   + page + '/PUU-XXII/2024</a>')
            return response_for('<html><body><main>' + row + '</main></body></html>', url)

        r = self.research(fresh_nonmatching_page)
        result = r.search('kb.putusan.ma.search', 'wanprestasi')
        self.assertEqual(result['outcome'], 'no_match')
        kemkes_calls = [u for u in r.fetcher.urls if 'kemkes' in u]
        self.assertEqual(len(kemkes_calls), rf.MAX_PAGE)

    def test_published_link_kept_exactly_as_published(self):
        r = self.research(kemkes_behavior())
        result = r.search('kb.putusan.ma.search', 'contoh dua')
        self.assertEqual(result['outcome'], 'match')
        self.assertEqual(result['records'][0]['source_url'],
                         'https://jdih.kemkes.go.id/putusan/'
                         'putusan-mahkamah-agung-nomor-123-kpdt2026-truncated-')

    def test_invalid_query_baseline_validation(self):
        r = self.research(kemkes_behavior())
        with self.assertRaises(DomainError):
            r.search('kb.putusan.ma.search', '')


def putusan3_down(url):
    raise NetworkError('connect_failed', 'The request could not be completed')


class AdvocateBlockerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(pr, 'PORTAL_GAP_SECONDS', 0.0)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_advocate_verify_error_passes_through_and_never_uses_alternative(self):
        r = FixtureResearch(Path(self.tmp.name) / 'adv.sqlite',
                            lambda url: response_for(PERADI_BROKEN, url))
        result = r.search('kb.advokat.verify', 'A.123456')
        self.assertEqual(result['outcome'], 'error')
        self.assertIn('peradi.or.id', result['portal']['url'])
        self.assertFalse(result['portal'].get('fallback', False))
        for url in r.fetcher.urls:
            self.assertNotIn('simpul.peradi.id', url)

    def test_advocate_lookup_is_never_cached(self):
        path = Path(self.tmp.name) / 'adv2.sqlite'
        r = FixtureResearch(path, lambda url: response_for(PERADI_BROKEN, url))
        r.search('kb.advokat.verify', 'A.123456')
        with sqlite3.connect(path) as db:
            rows = db.execute('SELECT count(*) FROM fallback_cache').fetchone()[0]
            index_rows = db.execute('SELECT count(*) FROM index_documents').fetchone()[0]
        self.assertEqual(rows, 0)
        self.assertEqual(index_rows, 0)

    def test_status_reports_honest_availability(self):
        status = rf.fallback_status()
        self.assertTrue(status['kb.putusan.ma.search']['available'])
        self.assertTrue(status['kb.putusan.pengadilan.search']['available'])
        self.assertFalse(status['kb.advokat.verify']['available'])
        self.assertIn('tidak dapat diverifikasi', status['kb.advokat.verify']['reason'])
        self.assertNotIn('simpul.peradi.id', json.dumps(rf.FALLBACK_PORTALS))


class FallbackFetcherPolicyTests(unittest.TestCase):
    def test_exact_host_https_enforced_every_hop(self):
        fetcher = rf.PortalFetcher(rf.FALLBACK_HOST_ALLOWLIST)
        with self.assertRaises(DomainError):
            fetcher._open_connection('93.184.216.34', 443, 'example.com', True, 1)
        with self.assertRaises(DomainError):
            fetcher._open_connection('93.184.216.34', 80, 'jdih.kemkes.go.id', False, 1)

    def test_default_fetcher_pins_single_host(self):
        pinned = rf.FallbackResearch._default_fetcher('jdih.kemkes.go.id')
        self.assertEqual(pinned.allowed_hosts, frozenset(('jdih.kemkes.go.id',)))
        self.assertIsInstance(pinned, pr.PortalFetcher)

    def test_blocked_hosts_are_never_in_the_fallback_allowlist(self):
        self.assertNotIn('jdih.mahkamahagung.go.id', rf.FALLBACK_HOST_ALLOWLIST)
        self.assertNotIn('kepaniteraan.mahkamahagung.go.id', rf.FALLBACK_HOST_ALLOWLIST)
        self.assertNotIn('simpul.peradi.id', rf.FALLBACK_HOST_ALLOWLIST)
        self.assertEqual(rf.FALLBACK_HOST_ALLOWLIST, frozenset(('jdih.kemkes.go.id',)))

    def test_parser_rejects_foreign_or_non_putusan_links(self):
        text = ('<a href="https://example.com/putusan/x">Putusan Contoh Portal Asing Nomor 1</a>'
                '<a href="https://jdih.kemkes.go.id/documents/x">Peraturan Contoh Nomor 2 Tahun</a>'
                '<a href="https://jdih.kemkes.go.id/putusan/putusan-mahkamah-agung-nomor-99">'
                'Putusan Mahkamah Agung Nomor 99 K/2026</a>')
        records = rf._parse_kemkes_putusan(text, 'https://jdih.kemkes.go.id/putusan')
        self.assertEqual([r['source_url'] for r in records],
                         ['https://jdih.kemkes.go.id/putusan/putusan-mahkamah-agung-nomor-99'])

    def test_parser_fails_closed_on_empty_or_foreign_structure(self):
        with self.assertRaises(PortalStructureError):
            rf._parse_kemkes_putusan(KEMKES_EMPTY, 'https://jdih.kemkes.go.id/putusan')


if __name__ == '__main__':
    unittest.main()
