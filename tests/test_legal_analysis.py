"""Independent unittest fixtures for backend/legal_engine/analysis.py (expert/ite/kuhp scope).

All sources, cases and evidence here are synthetic fixtures. They prove engine
behaviour (gates, retrieval-before-guidance, exact quotes, no asserted label
meanings, no verdicts, read-only), not real law.
"""
import hashlib
import json
import os
import re
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from backend.legal_engine import analysis as A  # noqa: E402

DISCLAIMER = 'Edukasi hukum, bukan nasihat hukum formal atau pengganti advokat.'
SCOPE = [
    'expert.ite.assess', 'expert.ite.unsur_breakdown', 'expert.ite.bukti_digital',
    'expert.pidana.assess', 'expert.pidana.unsur_breakdown', 'expert.pidana.sifat_perbuatan',
    'expert.perdata.assess', 'expert.perdata.wanprestasi', 'expert.perdata.perbuatan_lawan',
    'expert.keluarga.assess', 'expert.keluarga.nafkah',
    'expert.tenagakerja.assess', 'expert.konsumen.assess', 'expert.pdp.assess',
    'expert.opinion.generate',
    'ite.pasal.27', 'ite.pasal.27.ayat1', 'ite.pasal.27.ayat3', 'ite.pasal.27.ayat4',
    'ite.pasal.28', 'ite.pasal.28.ayat1', 'ite.pasal.28.ayat2',
    'ite.pasal.30', 'ite.pasal.30.ayat1', 'ite.pasal.32', 'ite.pasal.35', 'ite.pasal.45',
    'kuhp.pasal.pencurian', 'kuhp.pasal.penggelapan', 'kuhp.pasal.penipuan',
    'kuhp.pasal.pemerasan', 'kuhp.pasal.pengancaman', 'kuhp.pasal.pencemaran',
    'kuhp.pasal.penghinaan', 'kuhp.pasal.perlindungan_anak', 'kuhp.pasal.kdrt',
    'kuhp.pasal.pembunuhan', 'kuhp.pasal.penganiayaan', 'kuhp.pasal.perkosaan',
    'kuhp.pasal.korupsi', 'kuhp.pasal.narkotika', 'kuhp.baru.2026',
]


class TestError(Exception):
    def __init__(self, code, status=400, details=None):
        self.code = code
        self.status = status
        self.details = details
        super().__init__(code)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _src(sid, title, body, kind='uu'):
    return {'id': sid, 'title': title, 'url': 'https://fixture.example/peraturan/' + sid,
            'authority': 'fixture-jdhin', 'jurisdiction': 'ID', 'document_kind': kind,
            'body': body, 'fetched_at': '2026-10-01T00:00:00Z', 'sha256': _sha(body),
            'provenance': 'official_live_fetch', 'currentness': 'not_certified',
            'verification': {'official_host': True, 'fetched_by_server': True}}


S1_BODY = (
    'Pasal 27A: Setiap Orang dengan sengaja dan tanpa hak mendistribusikan muatan elektronik '
    'yang berisi pencemaran nama baik atau penghinaan. '
    'Pasal 27B: Setiap Orang dengan sengaja dan tanpa hak mendistribusikan muatan elektronik '
    'yang berisi berita bohong atau kebencian berdasarkan suku, agama, ras, dan antargolongan (sara). '
    'Pasal 27C: Setiap Orang mengirimkan muatan elektronik yang berisi ancaman kekerasan atau '
    'menakut-nakuti yang ditujukan secara pribadi. '
    'Pasal 28 ayat (1): Setiap Orang dengan sengaja dan tanpa hak mengakses elektronik milik orang lain. '
    'Pasal 28 ayat (2): Setiap Orang dengan sengaja dan tanpa hak menghasilkan gangguan terhadap '
    'sistem elektronik milik orang lain. '
    'Pasal 29: Setiap Orang dengan sengaja melakukan intersepsi atau penyadapan atas informasi elektronik. '
    'Pasal 45A (fixture): ancaman pidana bagi pelanggar ketentuan muatan elektronik ditetapkan '
    'dalam pasal sanksi. '
    'Pasal 5 (fixture): informasi elektronik dan dokumen elektronik merupakan alat bukti sah; '
    'bukti elektronik diperlakukan sesuai peraturan. '
    'Pasal 27B lanjutan (fixture): menimbulkan kekhawatiran melalui menakut-nakuti diperlakukan '
    'berbeda dari berita bohong.'
)
S2_BODY = (
    'Pasal 362: Barangsiapa mengambil barang milik orang lain dengan maksud dimiliki secara '
    'melawan hukum, diancam pencurian. '
    'Pasal 372 (fixture): pegawai yang menguasai barang milik atasan untuk dirinya diancam penggelapan. '
    'Pasal 378 (fixture): dengan tipu muslihat menggerakkan orang menyerahkan barang diancam penipuan. '
    'Pasal 368 (fixture): memaksa orang memberi barang dengan kekerasan atau ancaman diancam pemerasan. '
    'Pasal 369 (fixture): mengancam orang dengan kekerasan agar memberi keuntungan diancam pengancaman. '
    'Pasal 310 (fixture): menuduh sesuatu agar kehinaan orang lain diketahui umum adalah pencemaran nama baik. '
    'Pasal 312 (fixture): melukai nama baik atau menghina kehormatan seseorang dengan cara terbuka '
    'adalah penghinaan. '
    'Pasal 338 (fixture): merampas nyawa orang lain dengan sengaja diancam karena pembunuhan. '
    'Pasal 351 (fixture): menganiaya orang lain sehingga sakit diancam penganiayaan. '
    'Pasal 285 (fixture): memaksa wanita berkumpul dengannya di luar nikah dengan kekerasan '
    'diancam karena perkosaan.'
)
S3_BODY = (
    'Pasal 2 (fixture): ketentuan dalam kitab undang-undang hukum pidana baru diberlakukan '
    'secara berjenjang. '
    'Pasal 14 (fixture): perbuatan disebut dilakukan dengan kesengajaan bila pelakunya menghendaki '
    'atau mengyakini timbulnya akibat. '
    'Pasal 478 (fixture): contoh penomoran baru untuk tindak pidana pencurian dalam naskah ini.'
)

CORPUS = [
    _src('s1-ite-2024',
         '[FIXTURE] Undang-Undang Nomor 1 Tahun 2024 tentang Perubahan Kedua atas Undang-Undang '
         'Informasi dan Transaksi Elektronik', S1_BODY),
    _src('s2-kuhp-lama', '[FIXTURE] Kitab Undang-Undang Hukum Pidana cetak lama (label legacy uji)', S2_BODY),
    _src('s3-kuhp-baru', '[FIXTURE] Undang-Undang Nomor 1 Tahun 2023 tentang Kitab Undang-Undang Hukum Pidana', S3_BODY),
    _src('s4-pdp', '[FIXTURE] Undang-Undang tentang Pelindungan Data Pribadi',
         'Pasal 1 (fixture): setiap orang berhak atas pelindungan data pribadi dirinya. '
         'Pasal 20 (fixture): pengendali data pribadi wajib memastikan keamanan data.'),
    _src('s5-kerja', '[FIXTURE] Undang-Undang Ketenagakerjaan',
         'Pasal 50 (fixture): hubungan kerja mulai terjadi setelah adanya perjanjian kerja antara '
         'pekerja dan pengusaha. Pasal 151 (fixture): upah wajib dibayar tepat waktu. '
         'Pasal 36 (fixture): PHK adalah pengakhiran hubungan kerja.'),
    _src('s6-konsumen', '[FIXTURE] Undang-Undang Perlindungan Konsumen',
         'Pasal 4 (fixture): hak konsumen atas kenyamanan dan keamanan. '
         'Pasal 7 (fixture): kewajiban pelaku usaha memberi informasi yang benar.'),
    _src('s7-keluarga', '[FIXTURE] Undang-Undang Perkawinan',
         'Pasal 38 (fixture): perceraian hanya dapat dilakukan di hadapan sidang pengadilan. '
         'Pasal 41 (fixture): akibat perceraian meliputi kewajiban nafkah dan pemeliharaan anak.'),
    _src('s8-tipikor', '[FIXTURE] Undang-Undang Pemberantasan Tindak Pidana Korupsi',
         'Pasal 2 (fixture): setiap orang yang secara melawan hukum melakukan perbuatan memperkaya '
         'diri yang merugikan keuangan negara dikarenakan korupsi.'),
    _src('s9-narkotika', '[FIXTURE] Undang-Undang Narkotika',
         'Pasal 111 (fixture): tanpa hak menawarkan untuk dijual narkotika golongan I dipidana.'),
    _src('s10-pkdrt', '[FIXTURE] Undang-Undang Penghapusan Kekerasan Dalam Rumah Tangga',
         'Pasal 5 (fixture): dilarang melakukan kekerasan dalam rumah tangga terhadap orang dalam '
         'rumah tangganya.'),
    _src('s11-anak', '[FIXTURE] Undang-Undang Perlindungan Anak',
         'Pasal 76 (fixture): orang tua wajib melindungi anak. '
         'Pasal 80 (fixture): dilarang melakukan kekerasan terhadap anak.'),
    _src('s12-putusan-ma', '[FIXTURE] Putusan Mahkamah Agung No. 123 K/Kr/2024',
         'Majelis menilai terdakwa terbukti melakukan penghinaan dan pencemaran nama baik melalui '
         'media elektronik (fixture).', kind='putusan_ma'),
    _src('s13-kuhper', '[FIXTURE] Kitab Undang-Undang Hukum Perdata',
         'Pasal 1234 (fixture): setiap perikatan lahir dari perjanjian atau dari undang-undang. '
         'Pasal 1243 (fixture): wanprestasi memberi hak ganti rugi. '
         'Pasal 1365 (fixture): tiap perbuatan melawan hukum yang membawa kerugian mewajibkan ganti rugi.'),
]
CORPUS_BY_ID = dict((s['id'], s) for s in CORPUS)

CASE_OK = {'id': 'case_ok', 'jurisdiction': 'ID', 'occurred_on': '2026-03-15',
           'perspective': 'terlapor', 'topic': 'sengketa konten digital',
           'facts': 'Pengguna melaporkan bahwa akunnya digunakan tanpa izin untuk menyebarkan '
                    'tangkapan layar percakapan pada 15 Maret 2026 ke grup publik.',
           'questions': [], 'status': 'open'}
CASE_PELAPOR = dict(CASE_OK, id='case_pelapor', perspective='pelapor')
CASE_NO_DATE = dict(CASE_OK, id='case_no_date', occurred_on=None)
CASE_NO_JUR = dict(CASE_OK, id='case_no_jur', jurisdiction='')
CASE_FOREIGN = dict(CASE_OK, id='case_foreign', jurisdiction='SG')

EV_META = {'file_id': 'ev-1', 'case_id': 'case_ok', 'name': 'screenshot-fixture.png',
           'mime_type': 'image/png', 'size_bytes': 1234, 'sha256': 'b' * 64,
           'uploaded_at': '2026-10-20T00:00:00Z', 'encryption': 'AES-256-GCM',
           'status': 'ready', 'authenticity': 'not_verified', 'content_analysis': 'not_performed'}
EV_SENTINEL = b'SENTINEL-ORIGINAL-BYTES-NOT-FOR-ANALYSIS'


class FakeSources(object):
    def __init__(self, corpus, fail=False):
        self.corpus = corpus
        self.fail = fail
        self.search_calls = []

    def search(self, query, jurisdiction='ID', limit=10, kind=None):
        self.search_calls.append(query)
        if self.fail:
            raise TestError('collection_down', 503)
        needle = query.lower()
        out = []
        for r in self.corpus:
            if r.get('jurisdiction') != jurisdiction:
                continue
            if kind is not None and r.get('document_kind') != kind:
                continue
            if needle in (r['title'] + ' ' + r['body']).lower():
                out.append(r)
            if len(out) >= limit:
                break
        return out

    def get(self, source_id):
        for r in self.corpus:
            if r['id'] == source_id:
                return r
        raise TestError('not_found', 404)


class FakeEvidence(object):
    def __init__(self, items):
        self.items = items

    def read(self, file_id):
        if file_id not in self.items:
            raise TestError('not_found', 404)
        return self.items[file_id]


class FakeCtx(object):
    def __init__(self, cases=None, corpus=None, evidence=None, sources=None):
        self.now = '2026-10-24T01:02:03.000Z'
        self.actor = 'api-token:test-actor'
        self.tenant = 'tenant-hash-fixture'
        self._cases = cases or {}
        self.sources = sources if sources is not None else FakeSources(corpus if corpus is not None else CORPUS)
        self.evidence = evidence
        self.writes = []

    def get(self, kind, rec_id):
        if kind == 'legal_case':
            if rec_id in self._cases:
                return self._cases[rec_id]
            raise TestError('not_found', 404)
        raise TestError('not_found', 404)

    def list(self, kind, where=None, limit=1000):
        if kind == 'legal_evidence':
            recs = [dict(v[0], file_id=k) for k, v in (self.evidence.items.items() if self.evidence else [])]
            if where and 'case_id' in where:
                recs = [r for r in recs if r.get('case_id') == where['case_id']]
            return recs[:limit]
        return []

    def create(self, kind, data, rec_id=None):
        self.writes.append(('create', kind))
        raise TestError('writes_forbidden_in_test', 500)

    def update(self, kind, rec_id, changes, expected_version=None):
        self.writes.append(('update', kind))
        raise TestError('writes_forbidden_in_test', 500)

    def event(self, kind, data):
        self.writes.append(('event', kind))
        raise TestError('events_forbidden_in_test', 500)

    def assert_no_writes(self):
        self.writes == [] or self.fail('handler attempted writes: %r' % self.writes)


def make_ctx(**kw):
    with_evidence = kw.pop('with_evidence', False)
    ev = kw.pop('evidence', None)
    if ev is None and with_evidence:
        ev = FakeEvidence({'ev-1': (EV_META, EV_SENTINEL)})
    return FakeCtx(evidence=ev, **kw)


REF_RE = re.compile(r'[Pp]asal\s+([0-9]{1,4}[A-Za-z]?)(?:\s+ayat\s*\(([0-9]+)\))?')


def refs_of(text):
    out = []
    for m in REF_RE.finditer(text):
        ref = 'Pasal ' + m.group(1) + (' ayat (' + m.group(2) + ')' if m.group(2) else '')
        if ref not in out:
            out.append(ref)
    return out


class TestScope(unittest.TestCase):
    def test_exact_canonical_42_ids(self):
        self.assertEqual(sorted(A.HANDLERS.keys()), sorted(SCOPE))
        self.assertEqual(len(A.HANDLERS), 42)

    def test_registries_complete(self):
        self.assertEqual(set(A.SCHEMAS) == set(A.HANDLERS) == set(A.LIMITATIONS), True)
        self.assertEqual(A.MUTATIONS, set())

    def test_catalog_consistency_when_available(self):
        path = os.path.join(os.path.dirname(A.__file__), 'catalog_source.json')
        if not os.path.isfile(path):
            self.skipTest('catalog_source.json not present in worktree package')
        with open(path) as fh:
            cat = json.load(fh)
        ids = {s['id'] for s in cat['skills']
               if s['id'].startswith(('expert.', 'ite.', 'kuhp.'))}
        self.assertEqual(ids, set(SCOPE))

    def test_module_has_no_network_or_cli_imports(self):
        path = A.__file__
        with open(path) as fh:
            text = fh.read()
        banned = re.compile(r'^\s*(import|from)\s+(socket|subprocess|urllib|http|requests|'
                            r'telnetlib|smtplib|ftplib|ssl)', re.M)
        self.assertIsNone(banned.search(text), 'banned network/CLI import found')


class TestSchemas(unittest.TestCase):
    def test_strict_and_forbidden_fields(self):
        for sid, schema in A.SCHEMAS.items():
            self.assertIs(schema['additionalProperties'], False, sid)
            self.assertIn('case_id', schema['required'], sid)
            for bad in ('tenant', 'actor', 'path', 'status_verified', 'secrets'):
                self.assertNotIn(bad, schema['properties'], sid)

    def test_handler_rejects_unknown_fields(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        with self.assertRaises(A.DomainError) as cm:
            A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'case_ok', 'tenant': 'x'})
        self.assertEqual(cm.exception.code, 'unknown_fields')

    def test_handler_rejects_missing_case_id(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        with self.assertRaises(A.DomainError) as cm:
            A.HANDLERS['expert.pidana.assess'](ctx, {'question': 'hi'})
        self.assertEqual(cm.exception.code, 'missing_fields')

    def test_bukti_requires_evidence_file_id(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, with_evidence=True)
        with self.assertRaises(A.DomainError) as cm:
            A.HANDLERS['expert.ite.bukti_digital'](ctx, {'case_id': 'case_ok'})
        self.assertEqual(cm.exception.code, 'missing_fields')


class TestCaseFKAndGates(unittest.TestCase):
    def test_missing_case_is_404(self):
        ctx = make_ctx(cases={})
        with self.assertRaises(TestError) as cm:
            A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'ghost'})
        self.assertEqual(cm.exception.status, 404)

    def test_incident_date_gate_blocks_reading(self):
        ctx = make_ctx(cases={'case_no_date': CASE_NO_DATE})
        res = A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'case_no_date'})
        self.assertFalse(res['gates']['incident_date']['ok'])
        self.assertIsNone(res['reading'])
        self.assertEqual(res['reading_blocked_reason'], 'gates_open')
        self.assertTrue(res['clarification'])
        self.assertTrue(any('occurred_on' in c or 'Tanggal kejadian' in c for c in res['clarification']))
        self.assertFalse(res['no_verdict']['guilt_or_innocence_concluded'])

    def test_jurisdiction_gate_blocks_reading(self):
        ctx = make_ctx(cases={'case_no_jur': CASE_NO_JUR})
        res = A.HANDLERS['ite.pasal.27'](ctx, {'case_id': 'case_no_jur'})
        self.assertFalse(res['gates']['jurisdiction']['ok'])
        self.assertIsNone(res['reading'])
        self.assertTrue(res['retrieval']['jurisdiction_assumed'])
        self.assertEqual(res['retrieval']['jurisdiction_used'], 'ID')
        self.assertTrue(res['clarification'])

    def test_retrieval_still_runs_before_guidance_on_gate_failure(self):
        ctx = make_ctx(cases={'case_no_date': CASE_NO_DATE})
        self.assertEqual(ctx.sources.search_calls, [])
        A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'case_no_date'})
        self.assertGreater(len(ctx.sources.search_calls), 0)

    def test_foreign_jurisdiction_gets_no_indonesian_law_reading(self):
        ctx = make_ctx(cases={'case_foreign': CASE_FOREIGN})
        res = A.HANDLERS['kuhp.pasal.pencurian'](ctx, {'case_id': 'case_foreign'})
        self.assertEqual(res['retrieval']['jurisdiction_used'], 'SG')
        self.assertEqual(res['retrieval']['sources_found'], 0)
        self.assertIsNone(res['reading'])
        self.assertEqual(res['reading_blocked_reason'], 'no_relevant_source')

    def test_future_incident_date_asks_confirmation(self):
        ctx = make_ctx(cases={'case_future': dict(CASE_OK, id='case_future', occurred_on='2099-01-01')})
        res = A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'case_future'})
        self.assertFalse(res['gates']['incident_date']['ok'])
        self.assertEqual(res['gates']['incident_date']['reason'], 'future_date')
        self.assertTrue(any('konfirmasi' in c for c in res['clarification']))


class TestRetrievalAndQuotes(unittest.TestCase):
    def test_source_failure_means_no_guidance(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, sources=FakeSources(CORPUS, fail=True))
        res = A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'case_ok'})
        self.assertTrue(res['sources_unavailable'])
        self.assertIsNone(res['reading'])
        self.assertEqual(res['reading_blocked_reason'], 'sources_unavailable')
        self.assertEqual(res['citations'], [])
        self.assertTrue(res['retrieval']['failed'])
        self.assertTrue(res['lawyer_referral']['advised'])

    def test_empty_relevant_results_are_truthful_not_fabricated(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['ite.pasal.35'](ctx, {'case_id': 'case_ok'})
        self.assertGreaterEqual(res['retrieval']['sources_found'], 1)
        self.assertEqual(res['retrieval']['sources_relevant'], 0)
        self.assertTrue(res['no_relevant_source'])
        self.assertIsNone(res['reading'])
        self.assertEqual(res['citations'], [])

    def test_quotes_are_exact_substrings_with_full_citation_fields(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, with_evidence=True)
        for sid in SCOPE:
            args = {'case_id': 'case_ok'}
            if sid == 'expert.ite.bukti_digital':
                args['evidence_file_id'] = 'ev-1'
            res = A.HANDLERS[sid](ctx, args)
            for cit in res['citations']:
                self.assertEqual(set(cit.keys()),
                                 {'source_id', 'url', 'title', 'retrieved_at', 'sha256', 'quote'}, sid)
                src = CORPUS_BY_ID[cit['source_id']]
                self.assertIsInstance(cit['quote'], str)
                self.assertIn(cit['quote'], src['body'], sid)
                self.assertLessEqual(len(cit['quote']), 500, sid)
                self.assertEqual(cit['sha256'], src['sha256'], sid)
                self.assertEqual(cit['retrieved_at'], src['fetched_at'], sid)

    def test_reading_refs_match_independent_scan_of_quotes(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        for sid in ('ite.pasal.27', 'ite.pasal.27.ayat1', 'ite.pasal.27.ayat4',
                    'ite.pasal.30.ayat1', 'ite.pasal.45', 'kuhp.pasal.pencurian',
                    'kuhp.pasal.pembunuhan'):
            res = A.HANDLERS[sid](ctx, {'case_id': 'case_ok'})
            self.assertTrue(res['reading'], sid)
            for entry in res['reading']:
                self.assertEqual(entry['article_refs_in_quote'], refs_of(entry['quote']), sid)

    def test_untrusted_source_records_are_excluded(self):
        poisoned = dict(CORPUS[0], id='s1-poison', verification={}, provenance='model_memory')
        ctx = make_ctx(cases={'case_ok': CASE_OK}, corpus=CORPUS + [poisoned])
        res = A.HANDLERS['ite.pasal.27'](ctx, {'case_id': 'case_ok'})
        self.assertGreaterEqual(res['retrieval']['excluded_untrusted'], 1)
        used = {c['source_id'] for c in res['citations']}
        self.assertNotIn('s1-poison', used)


class TestRoutingIdentifiers(unittest.TestCase):
    def test_ite_label_never_asserted_as_meaning(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['ite.pasal.45'](ctx, {'case_id': 'case_ok'})
        self.assertEqual(res['routing']['claim_status'], 'untrusted_routing_label')
        self.assertIs(res['routing']['label_meaning_asserted'], False)
        self.assertEqual(res['routing']['identifier'], 'ite.pasal.45')
        self.assertTrue(res['reading'])
        quoted_refs = sorted({r for e in res['reading'] for r in e['article_refs_in_quote']})
        self.assertEqual(sorted(res['routing']['article_refs_in_quotes']), quoted_refs)

    def test_kuhp_old_numbering_not_default_current_law(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['kuhp.pasal.pencurian'](ctx, {'case_id': 'case_ok'})
        self.assertIn('362', res['transition']['label_articles'])
        self.assertIs(res['transition']['label_articles_asserted_as_current_law'], False)
        self.assertIs(res['transition']['automatic_resolution'], False)
        self.assertTrue(res['reading'])
        quoted = ' '.join(e['quote'] for e in res['reading'])
        self.assertIn('Pasal 362', refs_of(quoted) + (['Pasal 478'] if 'Pasal 478' in quoted else []))
        for e in res['reading']:
            self.assertIn('Pasal 362' in e['quote'] or 'Pasal 478' in e['quote'], [True], e['quote'])

    def test_kuhp_baru_no_effective_date_certification(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['kuhp.baru.2026'](ctx, {'case_id': 'case_ok'})
        self.assertEqual(res['routing']['claim_status'], 'untrusted_routing_label')
        self.assertIs(res['transition']['effective_date_certified'], False)
        self.assertIs(res['transition']['automatic_resolution'], False)
        self.assertEqual(res['transition']['dates_appearing_in_quotes'], [])
        self.assertTrue(res['reading'])
        self.assertIn('efektif 2026', res['routing']['label_claim'].lower())
        self.assertIs(res['routing']['label_meaning_asserted'], False)

    def test_every_routing_id_reports_label_as_untrusted(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        for sid in [s for s in SCOPE if s.startswith(('ite.pasal.', 'kuhp.'))]:
            args = {'case_id': 'case_ok'}
            res = A.HANDLERS[sid](ctx, args)
            self.assertEqual(res['routing']['claim_status'], 'untrusted_routing_label', sid)
            self.assertIs(res['routing']['label_meaning_asserted'], False, sid)


class TestExpertLenses(unittest.TestCase):
    def test_assess_full_flow_is_quote_grounded(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'case_ok'})
        self.assertTrue(res['gates_ok'])
        self.assertTrue(res['reading'])
        for entry in res['reading']:
            self.assertIn(entry['quote'], CORPUS_BY_ID[entry['source_id']]['body'])
            self.assertFalse(entry['is_conclusion'])
            self.assertTrue(entry['reading_prompt'])
        self.assertFalse(res['no_verdict']['legal_applicability_concluded'])
        self.assertEqual(res['narrative_as_recorded']['verification'], 'unverified_user_statement')
        self.assertEqual(res['narrative_as_recorded']['status'], 'allegation_until_verified')
        self.assertIn('metadata', res['system_recorded_facts']['note'])

    def test_perspective_shapes_neutral_rights(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK, 'case_pelapor': CASE_PELAPOR})
        ter = A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'case_ok'})
        pel = A.HANDLERS['expert.pidana.assess'](ctx, {'case_id': 'case_pelapor'})
        self.assertTrue(any('praduga tak bersalah' in q for q in ter['neutral_rights_questions']))
        self.assertTrue(any('melapor' in q for q in pel['neutral_rights_questions']))

    def test_missing_evidence_is_explicit(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['expert.ite.assess'](ctx, {'case_id': 'case_ok'})
        self.assertTrue(any('Belum ada bukti terhubung' in m for m in res['missing_evidence']))
        self.assertEqual(res['evidence']['linked_count'], 0)

    def test_sifat_perbuatan_no_decision(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['expert.pidana.sifat_perbuatan'](ctx, {'case_id': 'case_ok'})
        self.assertIs(res['sifat_perbuatan']['decision_given'], False)
        self.assertTrue(res['reading'])

    def test_unsur_breakdown_fragments_are_exact_quotes(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['expert.ite.unsur_breakdown'](ctx, {'case_id': 'case_ok', 'source_id': 's1-ite-2024'})
        self.assertTrue(res['fragments'])
        for frag in res['fragments']:
            self.assertIn(frag['quote'], S1_BODY)
            self.assertTrue(frag['reading_question'])
        self.assertEqual(res['breakdown_source']['source_id'], 's1-ite-2024')

    def test_unsur_breakdown_auto_source_and_404(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['expert.pidana.unsur_breakdown'](ctx, {'case_id': 'case_ok'})
        self.assertTrue(res['reading'])
        self.assertIn(res['citations'][0]['quote'], CORPUS_BY_ID[res['citations'][0]['source_id']]['body'])
        with self.assertRaises(TestError) as cm:
            A.HANDLERS['expert.pidana.unsur_breakdown'](ctx, {'case_id': 'case_ok', 'source_id': 'ghost'})
        self.assertEqual(cm.exception.status, 404)

    def test_unsur_gate_still_blocks(self):
        ctx = make_ctx(cases={'case_no_date': CASE_NO_DATE})
        res = A.HANDLERS['expert.ite.unsur_breakdown'](ctx, {'case_id': 'case_no_date'})
        self.assertEqual(res['reading_blocked_reason'], 'gates_open')
        self.assertNotIn('fragments', res)


class TestBuktiDigital(unittest.TestCase):
    def test_metadata_only_media_not_seen(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, with_evidence=True)
        res = A.HANDLERS['expert.ite.bukti_digital'](ctx, {'case_id': 'case_ok', 'evidence_file_id': 'ev-1'})
        media = res['media']
        self.assertIs(media['contents_seen'], False)
        self.assertEqual(media['content_analysis'], 'not_performed')
        self.assertIs(media['analysis_performed'], False)
        self.assertEqual(media['authenticity'], 'not_verified')
        self.assertEqual(media['sha256'], EV_META['sha256'])
        self.assertEqual(media['mime_type'], 'image/png')
        self.assertTrue(media['degraded_reasons'])
        payload = json.dumps(res)
        self.assertNotIn('SENTINEL', payload)
        self.assertNotIn(EV_SENTINEL.decode(), payload)

    def test_evidence_case_mismatch_rejected(self):
        other = dict(EV_META, file_id='ev-other', case_id='case_other')
        ev = FakeEvidence({'ev-1': (EV_META, EV_SENTINEL), 'ev-other': (other, EV_SENTINEL)})
        ctx = make_ctx(cases={'case_ok': CASE_OK}, evidence=ev)
        with self.assertRaises(A.DomainError) as cm:
            A.HANDLERS['expert.ite.bukti_digital'](
                ctx, {'case_id': 'case_ok', 'evidence_file_id': 'ev-other'})
        self.assertEqual(cm.exception.code, 'evidence_not_linked_to_case')
        self.assertEqual(cm.exception.status, 403)

    def test_unknown_evidence_file_is_404(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, with_evidence=True)
        with self.assertRaises(TestError) as cm:
            A.HANDLERS['expert.ite.bukti_digital'](
                ctx, {'case_id': 'case_ok', 'evidence_file_id': 'ghost'})
        self.assertEqual(cm.exception.status, 404)

    def test_evidence_api_absent_is_unavailable(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, evidence=None)
        with self.assertRaises(A.DomainError) as cm:
            A.HANDLERS['expert.ite.bukti_digital'](ctx, {'case_id': 'case_ok', 'evidence_file_id': 'ev-1'})
        self.assertEqual(cm.exception.code, 'evidence_api_unavailable')
        self.assertEqual(cm.exception.status, 501)


class TestOpinion(unittest.TestCase):
    def test_draft_opinion_structure_not_impersonated(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK})
        res = A.HANDLERS['expert.opinion.generate'](ctx, {'case_id': 'case_ok'})
        draft = res['draft_opinion']
        self.assertIs(draft['author']['is_licensed_lawyer'], False)
        self.assertIs(draft['author']['impersonates_lawyer'], False)
        self.assertEqual(draft['author']['kind'], 'automated_educational_draft')
        self.assertGreaterEqual(len(draft['sections']), 8)
        headings = [s['heading'] for s in draft['sections']]
        self.assertIn('Rujukan advokat', headings)
        self.assertIn('Disclaimer', headings)
        self.assertTrue(res['lawyer_referral']['advised'])


class TestUniversalEnvelope(unittest.TestCase):
    def _args(self, sid):
        args = {'case_id': 'case_ok'}
        if sid == 'expert.ite.bukti_digital':
            args['evidence_file_id'] = 'ev-1'
        return args

    def test_all_42_return_useful_distinct_output(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, with_evidence=True)
        seen_topics = set()
        for sid in SCOPE:
            res = A.HANDLERS[sid](ctx, self._args(sid))
            self.assertEqual(res['disclaimer'], DISCLAIMER, sid)
            self.assertIs(res['no_verdict']['guilt_or_innocence_concluded'], False, sid)
            self.assertIs(res['no_verdict']['legal_applicability_concluded'], False, sid)
            self.assertEqual(res['lens']['is_licensed_expert'], False, sid)
            self.assertTrue(res['retrieval']['performed'], sid)
            self.assertTrue(res['missing_evidence'], sid)
            self.assertTrue(res['neutral_rights_questions'], sid)
            self.assertTrue(res['lawyer_referral']['advised'], sid)
            self.assertEqual(res['limitations_ref'], sid)
            self.assertTrue(A.LIMITATIONS[sid], sid)
            self.assertEqual(res['gates']['jurisdiction']['value'], 'ID', sid)
            seen_topics.add(res['lens']['topic'])
        self.assertGreaterEqual(len(seen_topics), 30)

    def test_no_certainty_percent_fields(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, with_evidence=True)
        for sid in SCOPE:
            payload = json.dumps(A.HANDLERS[sid](ctx, self._args(sid)))
            self.assertNotIn('certainty_percent', payload)
            self.assertNotIn('tingkat kepastian', payload.lower())

    def test_handlers_are_read_only(self):
        ctx = make_ctx(cases={'case_ok': CASE_OK}, with_evidence=True)
        for sid in SCOPE:
            A.HANDLERS[sid](ctx, self._args(sid))
        self.assertEqual(ctx.writes, [])

    def test_limitations_are_explicit_and_specific(self):
        for sid in SCOPE:
            lines = A.LIMITATIONS[sid]
            self.assertIsInstance(lines, list)
            self.assertGreaterEqual(len(lines), 7, sid)
            self.assertTrue(any('not_certified' in ln for ln in lines), sid)
            if sid.startswith(('ite.pasal.', 'kuhp.')):
                self.assertTrue(any('routing kompatibilitas' in ln for ln in lines), sid)


if __name__ == '__main__':
    unittest.main(verbosity=2)
