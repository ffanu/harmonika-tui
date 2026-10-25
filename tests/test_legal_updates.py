"""UU updates layer tests: explicit-assertion amendment tracking, consolidation boundaries, in-app watches.

Real encrypted Store + real handler code path; official snapshots are stubbed at
the Sources boundary with exact record shapes produced by backend/legal_engine/sources.py.
No live network anywhere: socket.connect and the stub refresh both explode if touched.
"""
import copy
import hashlib
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from backend.legal_engine.common import DomainError
    from backend.legal_engine.store import Store, Context
    from backend.legal_engine.updates import (HANDLERS, SCHEMAS, MUTATIONS, LIMITATIONS,
                                              DISCLAIMER, K_RELATION, K_WATCH, K_EVENT)
    from backend.legal_engine import registry
    UPDATES = True
except ImportError:
    UPDATES = False

ACTOR = 'api-token:801'
ACTOR_B = 'api-token:802'

ITE_AMEND_BODY = (
    'UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 1 TAHUN 2024 TENTANG PERUBAHAN KEDUA ATAS '
    'UNDANG-UNDANG NOMOR 11 TAHUN 2008 TENTANG INFORMASI DAN TRANSAKSI ELEKTRONIK\n'
    'Pasal 1\n'
    'Beberapa ketentuan dalam Undang-Undang Nomor 11 Tahun 2008 tentang Informasi dan '
    'Transaksi Elektronik diubah sehingga Pasal 27 selanjutnya terbaca sebagai berikut.\n'
    'Pasal 2\n'
    'Undang-Undang ini mulai berlaku pada tanggal diundangkan.\n')
ITE_AMEND_QUOTE = ('Beberapa ketentuan dalam Undang-Undang Nomor 11 Tahun 2008 tentang '
                   'Informasi dan Transaksi Elektronik diubah sehingga Pasal 27 selanjutnya '
                   'terbaca sebagai berikut.')
ITE_AMEND_BODY_V2 = ITE_AMEND_BODY.replace('pada tanggal diundangkan.',
                                           'pada tanggal diundangkan dan diumumkan.')

DECOY_BODY = (
    'UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 1 TAHUN 2023 TENTANG KITAB UNDANG-UNDANG '
    'HUKUM PIDANA\n'
    'Pasal 1\n'
    'Ketentuan umum kitab ini mengubah cara pembacaan pasal pidana secara menyeluruh '
    'tanpa menyebut nomor undang-undang lain apa pun dalam teks ini.\n'
    'Pasal 2\n'
    'Ketentuan dalam kitab ini berlaku sejak tanggal yang ditetapkan oleh peraturan.\n')

PDP_BODY = (
    'UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 27 TAHUN 2022 TENTANG PELINDUNGAN DATA PRIBADI\n'
    'Pasal 1\n'
    'Ketentuan dasar pelindungan data pribadi mengatur pemrosesan yang bersifat spesifik '
    'dan terbatas.\n'
    'Pasal 2\n'
    'Pelindungan data pribadi dilaksanakan berdasarkan asas yang ditetapkan peraturan.\n'
    'Pasal 3\n'
    'Pelindungan data pribadi dilakukan secara terpadu dan menyeluruh antar lembaga.\n')

PDP_COMPARE_BODY = (
    'UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 27 TAHUN 2022 TENTANG PELINDUNGAN DATA PRIBADI\n'
    'Pasal 1\n'
    'Ketentuan dasar pelindungan data pribadi mengatur pemrosesan yang bersifat umum dan '
    'spesifik.\n'
    'Pasal 2\n'
    'Pelindungan data pribadi dilaksanakan berdasarkan asas yang ditetapkan peraturan.\n'
    'Pasal 4\n'
    'Pengendali data wajib memastikan hak subjek data dapat dilaksanakan sepenuhnya.\n')

PDP_AMENDER_BODY = (
    'UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 1 TAHUN 2026 TENTANG PENYESUAIAN PIDANA\n'
    'Pasal 1\n'
    'Ketentuan pidana dalam Undang-Undang Nomor 27 Tahun 2022 tentang Pelindungan Data '
    'Pribadi disesuaikan seperlunya menurut ketentuan ini.\n'
    'Pasal 2\n'
    'Undang-Undang ini mulai berlaku pada tanggal diundangkan.\n')
PDP_AMENDER_QUOTE = ('Ketentuan pidana dalam Undang-Undang Nomor 27 Tahun 2022 tentang '
                     'Pelindungan Data Pribadi disesuaikan seperlunya menurut ketentuan ini.')

CONS_BODY = (
    'DOKUMEN KONSOLIDASI RESMI ATAS UNDANG-UNDANG NOMOR 27 TAHUN 2022 TENTANG PELINDUNGAN '
    'DATA PRIBADI\n'
    'Pasal 1\n'
    'Dokumen ini merupakan konsolidasi resmi Undang-Undang Nomor 27 Tahun 2022 tentang '
    'Pelindungan Data Pribadi beserta perubahannya.\n')
CONS_QUOTE = ('Dokumen ini merupakan konsolidasi resmi Undang-Undang Nomor 27 Tahun 2022 '
              'tentang Pelindungan Data Pribadi beserta perubahannya.')


def snapshot(base, number, year, title, body, **overrides):
    sha = hashlib.sha256(body.encode('utf-8')).hexdigest()
    rec = {'id': base + '_' + sha[:12], 'base_id': base, 'title': title,
           'url': 'https://peraturan.go.id/files/' + base, 'authority': 'peraturan.go.id',
           'jurisdiction': 'ID', 'document_kind': 'uu', 'number': number, 'year': year,
           'body': body, 'fetched_at': '2026-10-24T00:00:00.000Z', 'sha256': sha,
           'raw_sha256': sha, 'provenance': 'official_live_fetch', 'currentness': 'not_certified',
           'verification': {'official_host': True, 'fetched_by_server': True,
                            'host_allowlisted': True, 'document_identity_verified': True,
                            'identity_basis': 'document_header_number_year_subject'},
           'document_form': 'html_full_text', 'text_availability': 'full_document_text',
           'has_full_text': True, 'citation_allowed': True, 'ttl_seconds': 86400,
           'scope': 'bounded_official_corpus_not_all_laws'}
    rec.update(overrides)
    return rec


class StubSources:
    def __init__(self):
        self.current = {}
        self.expired = False

    def put(self, rec):
        self.current[rec['base_id']] = rec

    def get(self, source_id):
        rec = None
        for r in self.current.values():
            if r['id'] == source_id or r['base_id'] == source_id:
                rec = r
        if rec is None:
            raise DomainError('source_not_found', 404)
        if self.expired:
            raise DomainError('source_refresh_required', 409)
        rec = copy.deepcopy(rec)
        if rec.get('has_full_text') and rec.get('verification', {}).get('document_identity_verified') is not True:
            raise DomainError('source_refresh_required', 409)
        return rec

    def search(self, query, jurisdiction='ID', limit=10, kind=None):
        return [copy.deepcopy(r) for r in self.current.values() if r.get('has_full_text')]

    def history(self, source_id):
        return []

    def refresh(self, *args, **kwargs):
        raise AssertionError('network refresh must never be attempted under a handler')


def default_corpus():
    stub = StubSources()
    stub.put(snapshot('uu_1_2024', 1, 2024, 'UU 1/2024 — Perubahan Kedua UU ITE', ITE_AMEND_BODY))
    stub.put(snapshot('uu_1_2023', 1, 2023, 'UU 1/2023 — KUHP baru', DECOY_BODY))
    stub.put(snapshot('uu_27_2022', 27, 2022, 'UU 27/2022 — PDP', PDP_BODY))
    stub.put(snapshot('uu_27_2022_compare', 27, 2022, 'UU 27/2022 — PDP (varian banding)', PDP_COMPARE_BODY))
    stub.put(snapshot('uu_1_2026', 1, 2026, 'UU 1/2026 — Penyesuaian Pidana', PDP_AMENDER_BODY))
    stub.put(snapshot('uu_27_2022_konsolidasi', 1, 2026, 'Konsolidasi resmi UU 27/2022', CONS_BODY))
    return stub


@unittest.skipUnless(UPDATES, 'legal updates venv required; mandatory suite run separately')
class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.sqlite', os.urandom(32))
        self.stub = default_corpus()
        no_socket = patch.object(socket.socket, 'connect', side_effect=AssertionError('network'))
        no_socket.start()
        self.addCleanup(no_socket.stop)

    def invoke(self, handler_key, args, actor=ACTOR, read_only=False):
        handler = HANDLERS[handler_key]
        with self.store.db() as d:
            ctx = Context(self.store, d, actor, sources=self.stub, read_only=read_only)
            return handler(ctx, args)

    def tenant_records(self, kind, actor=ACTOR):
        with self.store.db() as d:
            return Context(self.store, d, actor, sources=self.stub).list(kind)

    def tenant_get(self, kind, record_id, actor=ACTOR):
        with self.store.db() as d:
            return Context(self.store, d, actor, sources=self.stub).get(kind, record_id)


class RegistryContract(Harness):
    def test_three_canonical_ids_registered_with_valid_shapes(self):
        self.assertEqual(set(HANDLERS), {'uu.amandemen.track', 'uu.consolidated.get', 'uu.alert.new'})
        self.assertEqual(set(HANDLERS), set(SCHEMAS))
        self.assertLessEqual(set(HANDLERS), registry.IDS)
        self.assertLessEqual(set(MUTATIONS), set(HANDLERS))
        self.assertEqual(set(MUTATIONS), {'uu.amandemen.track', 'uu.alert.new'})
        self.assertNotIn('uu.consolidated.get', MUTATIONS)
        for skill, note in LIMITATIONS.items():
            self.assertTrue(note and isinstance(note, (list, str)), skill)
        loaded = registry.loaded()
        self.assertTrue(set(HANDLERS) <= set(loaded[0]))
        self.assertTrue({'uu.amandemen.track', 'uu.consolidated.get', 'uu.alert.new'} <= set(loaded[0]))

    def test_schemas_are_strict(self):
        with self.assertRaises(DomainError):
            self.invoke('uu.alert.new', {'action': 'list', 'unexpected': 1})
        with self.assertRaises(DomainError):
            self.invoke('uu.alert.new', {'action': 'email_me'})
        with self.assertRaises(DomainError):
            self.invoke('uu.amandemen.track', {'source_id': 'uu_27_2022', 'law_number': 27, 'law_year': 2022})
        with self.assertRaises(DomainError):
            self.invoke('uu.amandemen.track', {'relations': []})
        with self.assertRaises(DomainError):
            self.invoke('uu.consolidated.get', {})


class NoNetworkUnderHandler(Harness):
    def test_handlers_never_refresh_or_open_sockets(self):
        sub = self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2024'})
        self.assertTrue(sub['watch']['active'])
        self.invoke('uu.alert.new', {'action': 'check'})
        self.invoke('uu.amandemen.track', {'source_id': 'uu_27_2022'})
        self.invoke('uu.consolidated.get', {'source_id': 'uu_27_2022'})
        self.invoke('uu.alert.new', {'action': 'list'})


class MutationGating(Harness):
    def test_alert_writes_refused_on_read_only_context_and_reads_allowed(self):
        with self.assertRaises(DomainError) as e:
            self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2024'}, read_only=True)
        self.assertEqual(e.exception.code, 'mutation_not_authorized')
        self.assertEqual(e.exception.status, 403)
        out = self.invoke('uu.consolidated.get', {'source_id': 'uu_27_2022'}, read_only=True)
        self.assertIsNone(out['official_consolidated_document'])
        self.assertEqual(out['consolidation']['mode'], 'version_diff_workbench')
        listing = self.invoke('uu.alert.new', {'action': 'list'}, read_only=True)
        self.assertEqual(listing['watches'], [])

    def test_optional_case_must_exist_when_present(self):
        with self.assertRaises(DomainError) as e:
            self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2024', 'case_id': 'case-x'})
        self.assertEqual(e.exception.code, 'not_found')
        with self.store.db() as d:
            case = Context(self.store, d, ACTOR, sources=self.stub).create(
                'legal_case', {'topic': 'pdp', 'facts': 'fakta'})
        sub = self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2024', 'case_id': case['id']})
        self.assertEqual(sub['watch']['case_id'], case['id'])
        scoped = self.invoke('uu.alert.new', {'action': 'list', 'case_id': case['id']})
        self.assertEqual(len(scoped['watches']), 1)


class AlertWatchFlow(Harness):
    def test_actual_baseline_then_meaningful_change_detection_and_event_record(self):
        first = self.stub.current['uu_1_2024']
        sub = self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2024'})
        self.assertEqual(sub['watch']['status'], 'watching')
        self.assertEqual(sub['watch']['baseline_sha256'], first['sha256'])
        unchanged = self.invoke('uu.alert.new', {'action': 'check'})
        self.assertEqual(unchanged['changes'], [])
        self.assertEqual(unchanged['unchanged'], 1)
        self.assertEqual(unchanged['external_message_sent'], False)
        self.stub.put(snapshot('uu_1_2024', 1, 2024, 'UU 1/2024 — Perubahan Kedua UU ITE', ITE_AMEND_BODY_V2))
        changed = self.invoke('uu.alert.new', {'action': 'check'})
        self.assertEqual(len(changed['changes']), 1)
        entry = changed['changes'][0]
        self.assertEqual(entry['previous_sha256'], first['sha256'])
        self.assertEqual(entry['new_sha256'], self.stub.current['uu_1_2024']['sha256'])
        self.assertEqual(entry['event_new'], True)
        event = self.tenant_get(K_EVENT, entry['event_id'])
        self.assertEqual(event['change_kind'], 'official_snapshot_changed')
        self.assertEqual(event['delivery'], 'in_app_tenant_record_only')
        self.assertEqual(event['external_message_sent'], False)
        self.assertEqual(event['previous_sha256'], first['sha256'])
        watch = self.tenant_get(K_WATCH, 'watch-uu_1_2024')
        self.assertEqual(watch['baseline_sha256'], self.stub.current['uu_1_2024']['sha256'])
        self.assertEqual(watch['last_change']['event_id'], entry['event_id'])
        again = self.invoke('uu.alert.new', {'action': 'check'})
        self.assertEqual(again['changes'], [])
        self.assertEqual(again['unchanged'], 1)
        self.assertEqual(len(self.tenant_records(K_EVENT)), 1)
        listed = self.invoke('uu.alert.new', {'action': 'list'})
        self.assertEqual(listed['events_total'], 1)
        self.assertEqual(listed['events'][0]['new_sha256'], self.stub.current['uu_1_2024']['sha256'])

    def test_no_change_is_fully_idempotent_on_watch_record(self):
        self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_27_2022'})
        before = self.tenant_get(K_WATCH, 'watch-uu_27_2022')
        for _ in range(3):
            out = self.invoke('uu.alert.new', {'action': 'check'})
        after = self.tenant_get(K_WATCH, 'watch-uu_27_2022')
        self.assertEqual(out['changes'], [])
        self.assertEqual((after['version'], after['updated_at']),
                         (before['version'], before['updated_at']))
        self.assertEqual(self.tenant_records(K_EVENT), [])

    def test_baseline_awaited_when_no_trusted_snapshot_then_set_on_first_check(self):
        sub = self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_20_2025'})
        self.assertEqual(sub['watch']['status'], 'awaiting_baseline')
        self.assertIsNone(sub['watch']['baseline_sha256'])
        self.stub.put(snapshot('uu_20_2025', 20, 2025, 'UU 20/2025 — KUHAP', PDP_BODY))
        first = self.invoke('uu.alert.new', {'action': 'check'})
        self.assertEqual(first['baseline_set'], 1)
        self.assertEqual(first['changes'], [])
        watch = self.tenant_get(K_WATCH, 'watch-uu_20_2025')
        self.assertEqual(watch['status'], 'watching')
        self.assertEqual(watch['baseline_sha256'], self.stub.current['uu_20_2025']['sha256'])

    def test_resubscribe_never_touches_baseline_and_reactivation_resets_it(self):
        base = self.stub.current['uu_27_2022']
        self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_27_2022'})
        again = self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_27_2022'})
        self.assertTrue(again['already_subscribed'])
        self.assertTrue(again['baseline_unchanged_by_resubscribe'])
        watch = self.tenant_get(K_WATCH, 'watch-uu_27_2022')
        self.assertEqual(watch['version'], 1)
        self.stub.put(snapshot('uu_27_2022', 27, 2022, 'UU 27/2022 — PDP', PDP_COMPARE_BODY))
        off = self.invoke('uu.alert.new', {'action': 'unsubscribe', 'source_id': 'uu_27_2022'})
        self.assertFalse(off['watch']['active'])
        self.assertFalse(off['record_deleted'])
        self.assertEqual(self.tenant_get(K_WATCH, 'watch-uu_27_2022')['active'], False)
        off2 = self.invoke('uu.alert.new', {'action': 'unsubscribe', 'source_id': 'uu_27_2022'})
        self.assertTrue(off2['already_deactivated'])
        on = self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_27_2022'})
        self.assertTrue(on['reactivated'])
        self.assertEqual(on['watch']['baseline_sha256'], self.stub.current['uu_27_2022']['sha256'])
        checked = self.invoke('uu.alert.new', {'action': 'check'})
        self.assertEqual(checked['changes'], [])

    def test_deactivation_keeps_record_because_store_has_no_delete(self):
        self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_27_2022'})
        self.invoke('uu.alert.new', {'action': 'unsubscribe', 'source_id': 'uu_27_2022'})
        rec = self.tenant_get(K_WATCH, 'watch-uu_27_2022')
        self.assertFalse(rec['active'])
        self.assertEqual(rec['status'], 'deactivated')
        out = self.invoke('uu.alert.new', {'action': 'list'})
        self.assertEqual(len(out['watches']), 1)
        self.assertFalse(out['watches'][0]['active'])

    def test_wrong_tenant_sees_nothing_and_cannot_touch_foreign_watch(self):
        self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2024'})
        self.stub.put(snapshot('uu_1_2024', 1, 2024, 'UU 1/2024 — Perubahan Kedua UU ITE', ITE_AMEND_BODY_V2))
        changed = self.invoke('uu.alert.new', {'action': 'check'})
        event_id = changed['changes'][0]['event_id']
        other = self.invoke('uu.alert.new', {'action': 'check'}, actor=ACTOR_B)
        self.assertEqual(other['checked'], 0)
        self.assertEqual(other['changes'], [])
        listing = self.invoke('uu.alert.new', {'action': 'list'}, actor=ACTOR_B)
        self.assertEqual(listing['watches'], [])
        self.assertEqual(listing['events'], [])
        with self.assertRaises(DomainError) as e1:
            self.invoke('uu.alert.new', {'action': 'unsubscribe', 'watch_id': 'watch-uu_1_2024'}, actor=ACTOR_B)
        self.assertEqual((e1.exception.code, e1.exception.status), ('not_found', 404))
        with self.assertRaises(DomainError) as e2:
            self.invoke('uu.alert.new', {'action': 'check', 'watch_id': 'watch-uu_1_2024'}, actor=ACTOR_B)
        self.assertEqual(e2.exception.code, 'not_found')
        with self.assertRaises(DomainError) as e3:
            self.tenant_get(K_EVENT, event_id, actor=ACTOR_B)
        self.assertEqual(e3.exception.code, 'not_found')

    def test_malformed_or_unverified_snapshots_never_alert_or_corrupt_baseline(self):
        bad_sha = snapshot('uu_1_2024', 1, 2024, 'UU 1/2024', ITE_AMEND_BODY, sha256='deadbeef')
        self.stub.put(bad_sha)
        sub = self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2024'})
        self.assertEqual(sub['watch']['status'], 'awaiting_baseline')
        self.assertIsNone(sub['watch']['baseline_sha256'])
        checked = self.invoke('uu.alert.new', {'action': 'check'})
        self.assertEqual(checked['changes'], [])
        self.assertEqual(checked['unavailable'][0]['outcome'], 'source_degraded')
        watch = self.tenant_get(K_WATCH, 'watch-uu_1_2024')
        self.assertIsNone(watch['baseline_sha256'])
        self.assertEqual(self.tenant_records(K_EVENT), [])

        self.stub.put(snapshot('uu_1_2023', 1, 2023, 'KUHP', DECOY_BODY, provenance='user_claim'))
        self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2023'})
        out = self.invoke('uu.alert.new', {'action': 'check'})
        outcomes = {e['watch_id']: e['outcome'] for e in out['unavailable']}
        self.assertEqual(outcomes['watch-uu_1_2023'], 'source_degraded')
        self.assertIsNone(self.tenant_get(K_WATCH, 'watch-uu_1_2023')['baseline_sha256'])

        meta_only = snapshot('uu_27_2022', 27, 2022, 'PDP', '', has_full_text=False,
                             document_form='metadata_only', text_availability='metadata_only_no_document_text',
                             citation_allowed=False)
        self.stub.put(meta_only)
        sub3 = self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_27_2022'})
        self.assertEqual(sub3['watch']['status'], 'awaiting_baseline')
        out3 = self.invoke('uu.alert.new', {'action': 'check'})
        self.assertEqual(out3['changes'], [])

        strip = snapshot('uu_1_2026', 1, 2026, 'UU 1/2026', PDP_AMENDER_BODY)
        strip['verification']['document_identity_verified'] = False
        self.stub.put(strip)
        self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_1_2026'})
        out4 = self.invoke('uu.alert.new', {'action': 'check'})
        entry = [e for e in out4['unavailable'] if e['watch_id'] == 'watch-uu_1_2026'][0]
        self.assertEqual(entry['outcome'], 'source_unavailable')
        self.assertEqual(entry['reason'], 'source_refresh_required')
        self.assertEqual(self.tenant_records(K_EVENT), [])

    def test_expired_snapshots_reported_unavailable_and_baseline_untouched(self):
        self.invoke('uu.alert.new', {'action': 'subscribe', 'source_id': 'uu_27_2022'})
        before = self.tenant_get(K_WATCH, 'watch-uu_27_2022')
        self.stub.expired = True
        out = self.invoke('uu.alert.new', {'action': 'check'})
        self.assertEqual(out['unavailable'][0]['reason'], 'source_refresh_required')
        after = self.tenant_get(K_WATCH, 'watch-uu_27_2022')
        self.assertEqual((after['version'], after['baseline_sha256']),
                         (before['version'], before['baseline_sha256']))


class QuotedRelations(Harness):
    def relation(self, **overrides):
        rel = {'asserting_source_id': self.stub.current['uu_1_2024']['id'],
               'relation_type': 'amends', 'quote': ITE_AMEND_QUOTE,
               'target_number': 11, 'target_year': 2008}
        rel.update(overrides)
        return rel

    def test_valid_assertion_recorded_with_exact_citation_proof(self):
        out = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008,
                                              'relations': [self.relation()]})
        self.assertEqual(out['rejected'], [])
        self.assertEqual(len(out['recorded']), 1)
        self.assertTrue(out['recorded'][0]['persisted'])
        self.assertFalse(out['recorded'][0]['already_recorded'])
        self.assertEqual(len(out['relations']), 1)
        view = out['relations'][0]
        self.assertEqual(view['validation']['quote_exact_substring'], True)
        self.assertEqual(view['validation']['identity_citation_in_quote'], True)
        self.assertEqual(view['validation']['asserting_document_server_verified'], True)
        self.assertEqual(view['quote'], ITE_AMEND_QUOTE)
        self.assertTrue(view['article_refs_in_quote'])
        self.assertIn(ITE_AMEND_QUOTE, self.stub.current['uu_1_2024']['body'])

    def test_relation_quote_verification_expires_when_current_snapshot_rotates(self):
        args={'law_number':11,'law_year':2008,'relations':[self.relation()]}
        first=self.invoke('uu.amandemen.track',args)
        self.assertTrue(first['relations'][0]['reference_quote_verified'])
        rec=self.stub.current['uu_1_2024'];rec['body']+='\nPasal 99\nContoh revisi sintetis.';rec['sha256']=hashlib.sha256(rec['body'].encode()).hexdigest()
        second=self.invoke('uu.amandemen.track',{'law_number':11,'law_year':2008})
        self.assertFalse(second['relations'][0]['reference_quote_verified'])
        self.assertTrue(second['relations'][0]['validation']['quote_exact_substring'])
        self.assertFalse(second['relations'][0]['legal_relationship_verified'])

    def test_quote_must_be_exact_substring_and_must_carry_target_identity(self):
        out = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008,
                                              'relations': [
                                                  self.relation(quote='kalimat ini tidak ada dalam dokumen sumber.'),
                                                  self.relation(target_number=11, target_year=2009),
                                                  self.relation(quote=ITE_AMEND_QUOTE)]})
        codes = [r['error'] for r in out['rejected']]
        self.assertEqual(codes, ['relation_quote_not_exact', 'relation_identity_citation_missing'])
        self.assertEqual(len(out['relations']), 1)
        self.assertEqual(len(self.tenant_records(K_RELATION)), 1)

    def test_metadata_only_or_wrong_identity_assertor_rejected_and_self_loop_blocked(self):
        meta = snapshot('uu_1_2024', 1, 2024, 'PDP landing', '', has_full_text=False,
                        document_form='metadata_only', citation_allowed=False)
        self.stub.put(meta)
        out = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008,
                                              'relations': [self.relation()]})
        self.assertEqual(out['rejected'][0]['error'], 'asserting_source_not_trusted')
        self.assertEqual(out['relations'], [])
        trusted = snapshot('uu_1_2024', 1, 2024, 'UU 1/2024 — Perubahan Kedua UU ITE', ITE_AMEND_BODY)
        self.stub.put(trusted)
        out2 = self.invoke('uu.amandemen.track',
                           {'source_id': 'uu_1_2024',
                            'relations': [self.relation(target_number=1, target_year=2024,
                                                        quote='UNDANG-UNDANG REPUBLIK INDONESIA '
                                                              'NOMOR 1 TAHUN 2024 TENTANG PERUBAHAN '
                                                              'KEDUA ATAS')]})
        self.assertEqual(out2['rejected'][0]['error'], 'relation_self_loop')

    def test_candidates_require_explicit_assertion_keywords_never_promote(self):
        out = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008})
        self.assertEqual(out['relations'], [])
        self.assertFalse(out['inferred_from_keywords'])
        self.assertEqual([c['base_id'] for c in out['candidates']], ['uu_1_2024'])
        cand = out['candidates'][0]
        self.assertTrue(cand['requires_explicit_assertion'])
        self.assertFalse(cand['counted_as_amendment'])
        self.assertIn('NOMOR 11 TAHUN 2008', cand['excerpt'])
        self.assertIn('mengubah', DECOY_BODY)
        self.assertNotIn('uu_1_2023', [c['base_id'] for c in out['candidates']])
        asserted = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008,
                                                   'relations': [self.relation()]})
        self.assertEqual(len(asserted['relations']), 1)
        self.assertTrue(asserted['candidates'][0]['matching_recorded_relation'])

    def test_reassert_same_relation_is_idempotent_single_record(self):
        args = {'law_number': 11, 'law_year': 2008, 'relations': [self.relation()]}
        first = self.invoke('uu.amandemen.track', args)
        second = self.invoke('uu.amandemen.track', args)
        rec = self.tenant_records(K_RELATION)
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]['version'], 1)
        self.assertEqual(first['recorded'][0]['relation_id'], second['recorded'][0]['relation_id'])
        self.assertFalse(first['recorded'][0]['already_recorded'])
        self.assertTrue(second['recorded'][0]['already_recorded'])

    def test_dry_run_and_deactivate_without_delete(self):
        dry = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008,
                                              'relations': [self.relation()], 'persist': False})
        self.assertTrue(dry['recorded'][0]['persisted'] is False)
        self.assertEqual(self.tenant_records(K_RELATION), [])
        real = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008,
                                               'relations': [self.relation()]})
        rid = real['recorded'][0]['relation_id']
        off = self.invoke('uu.amandemen.track', {'action': 'deactivate', 'relation_id': rid})
        self.assertTrue(off['deactivated'])
        self.assertFalse(off['relation']['active'])
        self.assertTrue(off['record_retained'])
        self.assertEqual(len(self.tenant_records(K_RELATION)), 1)
        off2 = self.invoke('uu.amandemen.track', {'action': 'deactivate', 'relation_id': rid})
        self.assertTrue(off2['relation']['active'] is False)
        gone = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008})
        self.assertEqual(gone['relations'], [])
        self.assertEqual(gone['inactive_relations'], 1)
        with self.assertRaises(DomainError):
            self.invoke('uu.amandemen.track', {'action': 'deactivate'})


class ConsolidationBoundaries(Harness):
    def seed_pdp_relations(self, with_consolidation=True):
        self.invoke('uu.amandemen.track', {
            'law_number': 27, 'law_year': 2022,
            'relations': [{'asserting_source_id': self.stub.current['uu_1_2026']['id'],
                           'relation_type': 'amends', 'quote': PDP_AMENDER_QUOTE,
                           'target_number': 27, 'target_year': 2022}]})
        if with_consolidation:
            self.invoke('uu.amandemen.track', {
                'law_number': 27, 'law_year': 2022,
                'relations': [{'asserting_source_id': self.stub.current['uu_27_2022_konsolidasi']['id'],
                               'relation_type': 'consolidates', 'quote': CONS_QUOTE,
                               'target_number': 27, 'target_year': 2022}]})

    def test_workbench_is_explicit_draft_only_with_human_review_and_no_merged_text(self):
        self.seed_pdp_relations(with_consolidation=False)
        out = self.invoke('uu.consolidated.get', {'law_number': 27, 'law_year': 2022})
        self.assertIsNone(out['official_consolidated_document'])
        cons = out['consolidation']
        self.assertEqual(cons['mode'], 'version_diff_workbench')
        self.assertTrue(cons['draft_only'])
        self.assertFalse(cons['merged_statute_provided'])
        self.assertFalse(cons['invented_merged_statute'])
        self.assertFalse(cons['legally_complete'])
        self.assertTrue(cons['human_review_required'])
        self.assertEqual(out['patch_candidates'][0]['status'], 'candidate_requires_human_review')
        self.assertFalse(out['patch_candidates'][0]['applied'])
        self.assertFalse(out['patch_candidates'][0]['auto_merged'])
        self.assertFalse(out['amendment_graph_complete'])
        self.assertNotIn('consolidated_text', out)
        self.assertNotIn('body', (out['official_consolidated_document'] or {}))

    def test_official_consolidated_document_only_through_validated_assertion(self):
        self.seed_pdp_relations()
        with patch.dict('backend.legal_engine.updates.MANIFEST',{'uu_27_2022_konsolidasi':{'consolidated_for':{'number':27,'year':2022}}}):
            out = self.invoke('uu.consolidated.get', {'law_number': 27, 'law_year': 2022})
        official = out['official_consolidated_document']
        self.assertIsNotNone(official)
        self.assertEqual(official['sha256'], self.stub.current['uu_27_2022_konsolidasi']['sha256'])
        self.assertEqual(official['source_id'], self.stub.current['uu_27_2022_konsolidasi']['id'])
        self.assertEqual(official['basis'], 'server_configured_consolidation_and_exact_reference')
        self.assertFalse(official['legally_complete'])
        cons = out['consolidation']
        self.assertEqual(cons['mode'], 'official_consolidated_document')
        self.assertFalse(cons['draft_only'])
        self.assertFalse(cons['legally_complete'])
        self.assertFalse(cons['merged_text_composed_by_this_system'])
        self.assertNotIn('body', official)

    def test_caller_cannot_claim_an_arbitrary_document_is_official_consolidation(self):
        self.seed_pdp_relations()
        out=self.invoke('uu.consolidated.get',{'law_number':27,'law_year':2022})
        self.assertIsNone(out['official_consolidated_document'])
        self.assertTrue(out['consolidation']['draft_only'])
        self.assertFalse(out['relations'][0]['legal_relationship_verified'])

    def test_unavailable_consolidation_snapshot_degrades_to_workbench_not_invention(self):
        self.seed_pdp_relations()
        self.stub.expired = True
        out = self.invoke('uu.consolidated.get', {'law_number': 27, 'law_year': 2022})
        self.assertIsNone(out['official_consolidated_document'])
        self.assertEqual(out['consolidation']['mode'], 'version_diff_workbench')
        self.assertTrue(out['consolidation']['draft_only'])
        roles = [d['role'] for d in out['consolidation']['missing_dependencies']]
        self.assertIn('official_consolidated_snapshot', roles)
        self.assertIn('tracked_law_snapshot', roles)
        roles = {d['role']: d.get('reason') for d in out['consolidation']['missing_dependencies']}
        self.assertEqual(roles['official_consolidated_snapshot'], 'source_refresh_required')

    def test_exact_article_level_diff_is_textual_and_bounded(self):
        out = self.invoke('uu.consolidated.get', {'source_id': 'uu_27_2022',
                                               'compare_source_id': 'uu_27_2022_compare'})
        diff = out['version_diff']
        self.assertIsNotNone(diff)
        status = {a['article']: a['status'] for a in diff['articles']}
        self.assertEqual(status, {'1': 'textually_modified', '2': 'identical',
                                  '3': 'only_in_left', '4': 'only_in_right'})
        self.assertTrue(diff['textual_only'])
        self.assertTrue(diff['human_review_required'])
        self.assertFalse(diff['legal_effect_assessed'])
        self.assertFalse(diff['merged_statute_produced'])
        self.assertEqual(diff['left']['sha256'], self.stub.current['uu_27_2022']['sha256'])
        self.assertEqual(diff['right']['sha256'], self.stub.current['uu_27_2022_compare']['sha256'])

    def test_compare_requires_trusted_base_full_text(self):
        with self.assertRaises(DomainError) as e:
            self.invoke('uu.consolidated.get', {'law_number': 11, 'law_year': 2008,
                                             'compare_source_id': 'uu_27_2022_compare'})
        self.assertEqual(e.exception.code, 'base_full_text_required_for_compare')
        bad = snapshot('uu_27_2022', 27, 2022, 'PDP', PDP_BODY, provenance='user_claim')
        self.stub.put(bad)
        with self.assertRaises(DomainError) as e2:
            self.invoke('uu.consolidated.get', {'source_id': 'uu_27_2022',
                                             'compare_source_id': 'uu_27_2022_compare'})
        self.assertEqual(e2.exception.code, 'base_full_text_required_for_compare')

    def test_missing_dependencies_listed_for_unknown_tracked_law(self):
        out = self.invoke('uu.consolidated.get', {'law_number': 11, 'law_year': 2008})
        self.assertEqual(out['tracked_law']['snapshot_state'], 'not_in_official_corpus')
        self.assertIsNone(out['official_consolidated_document'])
        roles = [d['role'] for d in out['missing_dependencies']]
        self.assertIn('tracked_law_snapshot', roles)
        self.assertTrue(out['consolidation']['draft_only'])


class TrackIdentityResolution(Harness):
    def test_source_id_and_law_pair_resolve_consistently(self):
        by_source = self.invoke('uu.amandemen.track', {'source_id': 'uu_27_2022'})
        by_pair = self.invoke('uu.amandemen.track', {'law_number': 27, 'law_year': 2022})
        self.assertEqual(by_source['tracked_law']['number'], by_pair['tracked_law']['number'])
        self.assertEqual(by_source['tracked_law']['base_id'], 'uu_27_2022')
        self.assertEqual(by_pair['tracked_law']['base_id'], 'uu_27_2022')
        self.assertEqual(by_source['tracked_law']['snapshot_state'], 'trusted_full_text')
        outside = self.invoke('uu.amandemen.track', {'law_number': 11, 'law_year': 2008})
        self.assertEqual(outside['tracked_law']['snapshot_state'], 'not_in_official_corpus')
        self.assertEqual(outside['missing_dependencies'][0]['role'], 'tracked_law_snapshot')


if __name__ == '__main__':
    unittest.main(verbosity=2)

@unittest.skipUnless(UPDATES,'dedicated legal dependencies')
class ScheduledWatchRuntime(unittest.TestCase):
    def test_scheduler_only_visits_owned_active_watches_and_persists_cursor(self):
        from backend.legal_engine.runtime import Runtime
        from test_legal_core import FakeSources
        with tempfile.TemporaryDirectory() as tmp:
            rt=Runtime(Path(tmp),os.urandom(32),FakeSources());actor='api-token:899'
            rt.case_create(actor,{'topic':'fixture','facts':'fixture'},'fixturecase')
            def seed(d):Context(rt.store,d,actor).create(K_WATCH,{'active':True})
            rt.store.transaction(seed)
            seen=[]
            def invoke(a,s,args,key):seen.append((a,s,args,key));return {'status':'completed'}
            rt.invoke=invoke;out=rt.poll_watches()
            self.assertEqual(len(seen),1);self.assertEqual(seen[0][0],actor);self.assertEqual(seen[0][2],{'action':'check'});self.assertFalse(out['external_messages_sent'])
            with rt.store.db() as d:self.assertIsNotNone(d.execute("SELECT value FROM maintenance WHERE key='watch_cursor'").fetchone())
