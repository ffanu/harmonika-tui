"""Unified extensions tests: isolation, restart, encryption, idempotency, concurrency, verification."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent.parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from backend.unified_engine import extensions as ext  # noqa: E402
from backend.unified_engine.extensions import Extensions  # noqa: E402
from backend.unified_engine.state import State  # noqa: E402

ACTOR = 'api-token:1'
OTHER = 'api-token:2'
THIRD = 'api-token:3'


def make(directory, name='ext.db', key=b'unit-test-extension-key-0123456789'):
    state = State(str(Path(directory) / name), key)
    return state, Extensions(state)


def error(test, fn, code, status):
    with test.assertRaises(ValueError) as ctx:
        fn()
    test.assertEqual(ctx.exception.code, code)
    test.assertEqual(ctx.exception.status, status)


def document(identifier, text, **override):
    entry = {'id': identifier, 'title': 'Dok ' + identifier, 'text': text,
             'source': 'https://hai.harmonika.id/docs/' + identifier,
             'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'verified_at': '2026-01-15'}
    entry.update(override)
    return entry


def corpus_file(directory, documents, name='corpus.json'):
    path = Path(directory) / name
    path.write_text(json.dumps({'documents': documents}), encoding='utf-8')
    return path


class EventBus(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state, self.extensions = make(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_publish_shape_and_monotonic_sequence(self):
        first = self.extensions.publish(ACTOR, 'orders.created', {'order': 1}, 'idem-key-0001')
        second = self.extensions.publish(ACTOR, 'orders.created', {'order': 2}, 'idem-key-0002')
        self.assertEqual(set(first), {'event_id', 'sequence', 'topic', 'payload', 'timestamp', 'replayed'})
        self.assertFalse(first['replayed'])
        self.assertEqual((first['sequence'], second['sequence']), (1, 2))
        self.assertEqual(first['topic'], 'orders.created')
        self.assertEqual(first['payload'], {'order': 1})

    def test_publish_idempotent_replay_same_payload(self):
        first = self.extensions.publish(ACTOR, 't', {'a': 1}, 'idem-key-0001')
        again = self.extensions.publish(ACTOR, 't', {'a': 1}, 'idem-key-0001')
        self.assertTrue(again['replayed'])
        self.assertEqual(again['event_id'], first['event_id'])
        self.assertEqual(again['sequence'], first['sequence'])
        self.assertEqual(len(self.extensions.replay(ACTOR, 't')), 1)

    def test_publish_idempotent_key_order_independent(self):
        first = self.extensions.publish(ACTOR, 't', {'a': 1, 'b': 2}, 'idem-key-0001')
        again = self.extensions.publish(ACTOR, 't', {'b': 2, 'a': 1}, 'idem-key-0001')
        self.assertTrue(again['replayed'])
        self.assertEqual(again['event_id'], first['event_id'])

    def test_publish_idempotency_conflict_on_changed_payload(self):
        self.extensions.publish(ACTOR, 't', {'a': 1}, 'idem-key-0001')
        error(self, lambda: self.extensions.publish(ACTOR, 't', {'a': 2}, 'idem-key-0001'),
              'idempotency_conflict', 409)

    def test_idempotency_scoped_per_topic(self):
        one = self.extensions.publish(ACTOR, 't.one', {'a': 1}, 'idem-key-0001')
        two = self.extensions.publish(ACTOR, 't.two', {'a': 1}, 'idem-key-0001')
        self.assertFalse(two['replayed'])
        self.assertEqual((one['sequence'], two['sequence']), (1, 1))

    def test_sequences_independent_per_topic_and_actor(self):
        self.extensions.publish(ACTOR, 'a', {}, 'idem-key-0001')
        self.extensions.publish(ACTOR, 'a', {}, 'idem-key-0002')
        other = self.extensions.publish(OTHER, 'a', {}, 'idem-key-0001')
        self.assertEqual(other['sequence'], 1)

    def test_replay_after_sequence_and_limit(self):
        for index in range(5):
            self.extensions.publish(ACTOR, 'stream', {'i': index}, 'idem-key-%04d' % index)
        page = self.extensions.replay(ACTOR, 'stream', after_sequence=1, limit=2)
        self.assertEqual([event['sequence'] for event in page], [2, 3])
        rest = self.extensions.replay(ACTOR, 'stream', after_sequence=3)
        self.assertEqual([event['sequence'] for event in rest], [4, 5])

    def test_tenant_isolation_on_replay_and_publish(self):
        self.extensions.publish(ACTOR, 'private.topic', {'secret': 'x'}, 'idem-key-0001')
        self.assertEqual(self.extensions.replay(OTHER, 'private.topic'), [])
        own = self.extensions.publish(OTHER, 'private.topic', {'secret': 'y'}, 'idem-key-0001')
        self.assertEqual(own['sequence'], 1)
        self.assertEqual(len(self.extensions.replay(ACTOR, 'private.topic')), 1)

    def test_publisher_cannot_inject_actor(self):
        injected = {'actor': OTHER, 'principal': THIRD, 'topic': 'other.topic'}
        self.extensions.publish(ACTOR, 'real.topic', injected, 'idem-key-0001')
        self.assertEqual(self.extensions.replay(OTHER, 'real.topic'), [])
        self.assertEqual(self.extensions.replay(THIRD, 'real.topic'), [])
        stored = self.extensions.replay(ACTOR, 'real.topic')[0]
        self.assertEqual(stored['payload'], injected)

    def test_topic_validation(self):
        for bad in ('', 'has space', 'bad/chars!', 'x' * 81, 'topik#tag', 7):
            error(self, lambda bad=bad: self.extensions.publish(ACTOR, bad, {}, 'idem-key-0001'),
                  'invalid_topic', 400)
        error(self, lambda: self.extensions.replay(ACTOR, 'no!'), 'invalid_topic', 400)

    def test_payload_limit_16kib_and_non_dict_rejected(self):
        self.extensions.publish(ACTOR, 't', {'t': 'a' * 16300}, 'idem-key-0001')
        error(self, lambda: self.extensions.publish(ACTOR, 't', {'t': 'a' * 17000}, 'idem-key-0002'),
              'event_payload_limit', 413)
        error(self, lambda: self.extensions.publish(ACTOR, 't', ['not', 'dict'], 'idem-key-0003'),
              'invalid_payload', 400)
        error(self, lambda: self.extensions.publish(ACTOR, 't', {'x': {'y': object()}}, 'idem-key-0004'),
              'invalid_json', 400)

    def test_idem_key_format(self):
        for bad in ('short', 'x' * 7, 'x' * 129, 'kunci-é', 42):
            error(self, lambda bad=bad: self.extensions.publish(ACTOR, 't', {}, bad),
                  'idempotency_required', 400)

    def test_replay_input_validation(self):
        error(self, lambda: self.extensions.replay(ACTOR, 't', after_sequence=-1), 'invalid_after_sequence', 400)
        error(self, lambda: self.extensions.replay(ACTOR, 't', after_sequence='0'), 'invalid_after_sequence', 400)
        error(self, lambda: self.extensions.replay(ACTOR, 't', limit=0), 'invalid_limit', 400)
        error(self, lambda: self.extensions.replay(ACTOR, 't', limit=101), 'invalid_limit', 400)

    def test_topic_quota_per_actor(self):
        with patch.object(ext, 'MAX_TOPICS_PER_ACTOR', 2):
            self.extensions.publish(ACTOR, 'a', {}, 'idem-key-0001')
            self.extensions.publish(ACTOR, 'b', {}, 'idem-key-0002')
            error(self, lambda: self.extensions.publish(ACTOR, 'c', {}, 'idem-key-0003'), 'topic_quota', 429)
            self.extensions.publish(ACTOR, 'b', {}, 'idem-key-0004')  # existing topic still allowed
            self.extensions.publish(OTHER, 'c', {}, 'idem-key-0005')  # quota is per actor

    def test_event_quota_per_actor(self):
        with patch.object(ext, 'MAX_EVENTS_PER_ACTOR', 3):
            for index in range(3):
                self.extensions.publish(ACTOR, 't', {'i': index}, 'idem-key-%04d' % index)
            error(self, lambda: self.extensions.publish(ACTOR, 't', {}, 'idem-key-0009'), 'event_quota', 429)
            self.extensions.publish(OTHER, 't', {}, 'idem-key-0009')

    def test_global_event_capacity(self):
        with patch.object(ext, 'MAX_EVENTS_GLOBAL', 3):
            self.extensions.publish(ACTOR, 'a', {}, 'idem-key-0001')
            self.extensions.publish(ACTOR, 'b', {}, 'idem-key-0002')
            self.extensions.publish(OTHER, 'a', {}, 'idem-key-0003')
            error(self, lambda: self.extensions.publish(THIRD, 'a', {}, 'idem-key-0004'), 'event_capacity', 429)

    def test_ttl_expiry_cleanup(self):
        self.extensions.publish(ACTOR, 't', {'x': 1}, 'idem-key-0001')
        connection = sqlite3.connect(str(self.state.store.path))
        connection.execute('UPDATE unified_ext_events SET expires=1')
        connection.commit()
        connection.close()
        self.assertEqual(self.extensions.replay(ACTOR, 't'), [])
        fresh = self.extensions.publish(ACTOR, 't', {'x': 2}, 'idem-key-0002')
        self.assertEqual(fresh['sequence'], 2)  # durable cursor does not reset when TTL prunes payloads

    def test_database_stores_no_plaintext_topic_or_payload(self):
        marker = 'plaintext-should-not-persist'
        self.extensions.publish(ACTOR, 'secret.business.topic', {'note': marker}, 'idem-key-0001')
        raw = Path(self.state.store.path).read_bytes()
        self.assertNotIn(b'secret.business.topic', raw)
        self.assertNotIn(marker.encode(), raw)
        with sqlite3.connect(str(self.state.store.path)) as db:
            columns = db.execute('SELECT id,actor,topic,seq,idem,digest,created,expires '
                                 'FROM unified_ext_events').fetchall()
        for row in columns:
            for value in row:
                self.assertNotIn('secret.business.topic', str(value))
                self.assertNotIn(marker, str(value))

    def test_tampered_sealed_event_fails_closed(self):
        self.extensions.publish(ACTOR, 't', {'x': 1}, 'idem-key-0001')
        connection = sqlite3.connect(str(self.state.store.path))
        connection.execute('UPDATE unified_ext_events SET sealed=zeroblob(length(sealed))')
        connection.commit()
        connection.close()
        error(self, lambda: self.extensions.replay(ACTOR, 't'), 'state_integrity_failure', 503)

    def test_concurrent_publish_yields_unique_monotonic_sequences(self):
        results, failures = [], []
        barrier = threading.Barrier(8)

        def worker(number):
            barrier.wait()
            for index in range(5):
                try:
                    results.append(self.extensions.publish(
                        ACTOR, 'concurrent', {'w': number, 'i': index}, 'idem-%d-%d' % (number, index)))
                except ValueError as exc:
                    failures.append(str(exc))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        sequences = sorted(event['sequence'] for event in results)
        self.assertEqual(sequences, list(range(1, 41)))
        stored = [event['sequence'] for event in self.extensions.replay(ACTOR, 'concurrent', limit=100)]
        self.assertEqual(sorted(stored), list(range(1, 41)))

    def test_restart_durability(self):
        self.extensions.publish(ACTOR, 'durable', {'k': 'v'}, 'idem-key-0001')
        state2, extensions2 = make(self.directory.name)
        self.addCleanup(state2.store.close)
        events = extensions2.replay(ACTOR, 'durable')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['payload'], {'k': 'v'})
        replayed = extensions2.publish(ACTOR, 'durable', {'k': 'v'}, 'idem-key-0001')
        self.assertTrue(replayed['replayed'])

    def test_event_limits_match_spec(self):
        self.assertEqual(ext.MAX_EVENT_PAYLOAD_BYTES, 16384)
        self.assertEqual(ext.MAX_TOPICS_PER_ACTOR, 100)
        self.assertEqual(ext.MAX_EVENTS_PER_ACTOR, 1000)
        self.assertEqual(ext.MAX_EVENTS_GLOBAL, 10000)
        self.assertEqual(ext.EVENT_TTL_SECONDS, 86400)

    def test_invalid_principal_rejected(self):
        error(self, lambda: self.extensions.publish('attacker', 't', {}, 'idem-key-001'),
              'invalid_principal', 403)


class Knowledge(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state, self.extensions = make(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_unloaded_corpus_is_unavailable_503(self):
        error(self, lambda: self.extensions.query('apa saja'), 'corpus_unavailable', 503)

    def test_load_and_query_with_exact_citations(self):
        path = corpus_file(self.directory.name, [
            document('api-chat', 'Endpoint chat Harmonika menerima pesan dan mengembalikan balasan.'),
            document('api-jobs', 'Endpoint jobs menjalankan tugas asinkron dengan idempotency key.'),
        ])
        loaded = self.extensions.load_corpus(path)
        self.assertEqual(loaded['documents'], 2)
        self.assertFalse(loaded['vector_db'])
        result = self.extensions.query('chat pesan')
        self.assertEqual(result['method'], 'local_lexical_bm25')
        self.assertFalse(result['vector_db'])
        self.assertFalse(result['independently_verified'])
        self.assertEqual(result['corpus_documents'], 2)
        top = result['results'][0]
        self.assertEqual(top['id'], 'api-chat')
        self.assertEqual(set(top['citation']), {'source', 'sha256', 'verified_at'})
        self.assertEqual(top['citation']['sha256'],
                         hashlib.sha256('Endpoint chat Harmonika menerima pesan dan mengembalikan balasan.'
                                        .encode('utf-8')).hexdigest())
        self.assertEqual(top['citation']['source'], 'https://hai.harmonika.id/docs/api-chat')
        self.assertEqual(top['citation']['verified_at'], '2026-01-15')
        self.assertFalse(top['independently_verified'])

    def test_hash_mismatch_fails_closed(self):
        path = corpus_file(self.directory.name, [document('bad', 'teks', sha256='0' * 64)])
        error(self, lambda: self.extensions.load_corpus(path), 'corpus_hash_mismatch', 400)
        error(self, lambda: self.extensions.query('teks'), 'corpus_unavailable', 503)

    def test_failed_reload_retains_previous_valid_corpus(self):
        good = corpus_file(self.directory.name, [document('ok', 'konten valid')], name='good.json')
        self.extensions.load_corpus(good)
        bad = corpus_file(self.directory.name, [document('ok2', 'konten rusak', sha256='f' * 64)], name='bad.json')
        error(self, lambda: self.extensions.load_corpus(bad), 'corpus_hash_mismatch', 400)
        self.assertEqual(self.extensions.query('valid')['results'][0]['id'], 'ok')

    def test_duplicate_document_id_rejected(self):
        path = corpus_file(self.directory.name, [document('dup', 'satu'), document('dup', 'dua')])
        error(self, lambda: self.extensions.load_corpus(path), 'corpus_duplicate_id', 400)

    def test_document_fields_strict(self):
        base = document('x', 'teks')
        missing = {k: v for k, v in base.items() if k != 'verified_at'}
        error(self, lambda: self.extensions.load_corpus(
            corpus_file(self.directory.name, [missing], 'm.json')), 'corpus_invalid', 400)
        extra = dict(base, extra=1)
        error(self, lambda: self.extensions.load_corpus(
            corpus_file(self.directory.name, [extra], 'e.json')), 'corpus_invalid', 400)

    def test_untrusted_source_scheme_rejected(self):
        for source in ('ftp://files.example.com/doc', 'file:///etc/passwd', 'gopher://x'):
            entry = document('s', 'teks', source=source)
            error(self, lambda entry=entry: self.extensions.load_corpus(
                corpus_file(self.directory.name, [entry], source[0] + '.json')),
                'untrusted_corpus_source', 400)
        trusted = corpus_file(self.directory.name, [document('h', 'teks', source='https://hai.harmonika.id/x')])
        self.assertEqual(self.extensions.load_corpus(trusted)['documents'], 1)

    def test_invalid_verified_at_rejected(self):
        for date in ('2026-13-40', '15-01-2026', '2026-01-15T00:00:00Z'):
            entry = document('d', 'teks', verified_at=date)
            error(self, lambda entry=entry: self.extensions.load_corpus(
                corpus_file(self.directory.name, [entry], date[0] + '.json')), 'invalid_corpus_date', 400)

    def test_query_without_match_returns_empty_honestly(self):
        self.extensions.load_corpus(corpus_file(self.directory.name, [document('a', 'faktur pajak')]))
        result = self.extensions.query('kuantum superkonduktor')
        self.assertEqual(result['results'], [])
        self.assertFalse(result['independently_verified'])

    def test_bm25_ranks_more_relevant_document_first(self):
        self.extensions.load_corpus(corpus_file(self.directory.name, [
            document('both', 'pembayaran faktur dan pembayaran tagihan serta pembayaran cicilan'),
            document('one', 'faktur diterbitkan toko'),
            document('none', 'dokumen lain tentang cuaca'),
        ]))
        results = self.extensions.query('pembayaran faktur', limit=3)['results']
        self.assertEqual([item['id'] for item in results], ['both', 'one'])
        self.assertGreater(results[0]['score'], results[1]['score'])

    def test_missing_corpus_file_and_no_network(self):
        error(self, lambda: self.extensions.load_corpus(self.directory.name + '/absent.json'),
              'corpus_not_found', 404)
        error(self, lambda: self.extensions.load_corpus('https://hai.harmonika.id/corpus.json'),
              'corpus_not_found', 404)  # only local operator files; URLs are never fetched

    def test_query_input_validation(self):
        self.extensions.load_corpus(corpus_file(self.directory.name, [document('a', 'teks')]))
        error(self, lambda: self.extensions.query('   '), 'invalid_query', 400)
        error(self, lambda: self.extensions.query('x' * 401), 'invalid_query', 400)
        error(self, lambda: self.extensions.query('teks', limit=0), 'invalid_limit', 400)
        error(self, lambda: self.extensions.query('teks', limit=21), 'invalid_limit', 400)

    def test_result_citations_carry_hash_date_not_certification(self):
        text = 'syarat kuota dan batas payload dijelaskan di sini'
        self.extensions.load_corpus(corpus_file(self.directory.name, [document('q', text)]))
        item = self.extensions.query('kuota payload')['results'][0]
        self.assertEqual(item['citation']['sha256'], hashlib.sha256(text.encode('utf-8')).hexdigest())
        self.assertEqual(item['citation']['verified_at'], '2026-01-15')
        self.assertFalse(item['independently_verified'])  # caller can never certify truth


class Secrets(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state, self.extensions = make(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_set_and_metadata_roundtrip_without_value(self):
        self.extensions.secret_set(ACTOR, 'crm.api-token', 'tok_live_abcd1234', 'idem-key-0001')
        metadata = self.extensions.secret_metadata(ACTOR, 'crm.api-token')
        self.assertEqual(set(metadata), {'name', 'created_at', 'updated_at'})
        self.assertNotIn('value', metadata)
        self.assertNotIn('last4', metadata)
        self.assertNotIn('tok_live_abcd1234', json.dumps(metadata))

    def test_set_idempotent_and_conflict(self):
        first = self.extensions.secret_set(ACTOR, 'mail.smtp', 'v1', 'idem-key-0001')
        again = self.extensions.secret_set(ACTOR, 'mail.smtp', 'v1', 'idem-key-0001')
        self.assertFalse(first['replayed'])
        self.assertTrue(again['replayed'])
        error(self, lambda: self.extensions.secret_set(ACTOR, 'mail.smtp', 'v2', 'idem-key-0001'),
              'idempotency_conflict', 409)
        replaced = self.extensions.secret_set(ACTOR, 'mail.smtp', 'v2', 'idem-key-0002')
        self.assertFalse(replaced['replayed'])
        self.assertEqual(replaced['created_at'], first['created_at'])

    def test_value_never_echoed_or_stored_plaintext(self):
        secret = 'SUPER-SECRET-VALUE-9f1c'
        self.extensions.secret_set(ACTOR, 'wa.webhook', secret, 'idem-key-0001')
        raw = Path(self.state.store.path).read_bytes()
        self.assertNotIn(secret.encode(), raw)
        self.assertNotIn(b'wa.webhook', raw)  # exact names hashed, not stored
        metadata = self.extensions.secret_metadata(ACTOR, 'wa.webhook')
        self.assertNotIn(secret, json.dumps(metadata))
        removed = self.extensions.secret_delete(ACTOR, 'wa.webhook', 'idem-key-0002')
        self.assertNotIn(secret, json.dumps(removed))
        try:
            self.extensions.secret_metadata(ACTOR, 'wa.webhook')
        except ValueError as exc:
            self.assertNotIn(secret, str(exc))
        else:
            self.fail('metadata after delete should 404')

    def test_resolve_internal_purpose_allowlist(self):
        self.extensions.secret_set(ACTOR, 'erp.connector', 'conn-value-77', 'idem-key-0001')
        self.assertEqual(self.extensions.secret_resolve(ACTOR, 'erp.connector', 'internal_connector'),
                         'conn-value-77')
        for purpose in ('integration', 'runtime', 'export', '', None):
            error(self, lambda purpose=purpose: self.extensions.secret_resolve(ACTOR, 'erp.connector', purpose),
                  'purpose_not_allowed', 403)

    def test_resolve_scoped_and_unknown(self):
        self.extensions.secret_set(ACTOR, 'erp.connector', 'v', 'idem-key-0001')
        error(self, lambda: self.extensions.secret_resolve(ACTOR, 'absent', 'internal_connector'),
              'not_found', 404)
        error(self, lambda: self.extensions.secret_resolve(OTHER, 'erp.connector', 'internal_connector'),
              'not_found', 404)  # cross-tenant lookup impossible

    def test_delete_lifecycle(self):
        self.extensions.secret_set(ACTOR, 'tmp.key', 'v', 'idem-key-0001')
        self.assertEqual(self.extensions.secret_delete(ACTOR, 'tmp.key', 'idem-key-0002'),
                         {'name': 'tmp.key', 'deleted': True, 'replayed': False})
        error(self, lambda: self.extensions.secret_metadata(ACTOR, 'tmp.key'), 'not_found', 404)
        error(self, lambda: self.extensions.secret_resolve(ACTOR, 'tmp.key', 'internal_connector'),
              'not_found', 404)
        error(self, lambda: self.extensions.secret_delete(ACTOR, 'tmp.key', 'idem-key-0003'), 'not_found', 404)
        error(self, lambda: self.extensions.secret_delete(ACTOR, 'tmp.key', 'short'), 'idempotency_required', 400)

    def test_reserved_names_rejected(self):
        for name in ('root', 'provider', 'env', 'system', 'Master'):
            error(self, lambda name=name: self.extensions.secret_set(ACTOR, name, 'v', 'idem-key-0001'),
                  'secret_name_reserved', 403)

    def test_invalid_names_rejected(self):
        for name in ('../etc/passwd', 'a/b', '', ' leading', 'x' * 65, '_underscore', None):
            error(self, lambda name=name: self.extensions.secret_set(ACTOR, name, 'v', 'idem-key-0001'),
                  'invalid_secret_name', 400)

    def test_value_limits(self):
        error(self, lambda: self.extensions.secret_set(ACTOR, 'big', 'x' * 4097, 'idem-key-0001'),
              'secret_value_limit', 413)
        error(self, lambda: self.extensions.secret_set(ACTOR, 'empty', '', 'idem-key-0001'),
              'invalid_secret_value', 400)
        error(self, lambda: self.extensions.secret_set(ACTOR, 'num', 12345, 'idem-key-0001'),
              'invalid_secret_value', 400)
        self.extensions.secret_set(ACTOR, 'ok', 'x' * 4096, 'idem-key-0002')

    def test_secret_quotas(self):
        with patch.object(ext, 'MAX_SECRETS_PER_ACTOR', 2):
            for index in range(2):
                self.extensions.secret_set(ACTOR, 'n%d' % index, 'v', 'idem-key-%04d' % index)
            error(self, lambda: self.extensions.secret_set(ACTOR, 'n9', 'v', 'idem-key-0009'),
                  'secret_quota', 429)
            self.extensions.secret_set(OTHER, 'n9', 'v', 'idem-key-0009')
        with patch.object(ext, 'MAX_SECRETS_GLOBAL', 4):  # 3 secrets already stored above
            self.extensions.secret_set(THIRD, 'g1', 'v', 'idem-key-0001')
            error(self, lambda: self.extensions.secret_set(THIRD, 'g2', 'v', 'idem-key-0002'),
                  'secret_capacity', 429)

    def test_ciphertext_physical_bound(self):
        with patch.object(ext, 'MAX_SECRET_ACTOR_CIPHERTEXT', 300):
            self.extensions.secret_set(ACTOR, 'a', 'x' * 200, 'idem-key-0001')
            error(self, lambda: self.extensions.secret_set(ACTOR, 'b', 'y' * 200, 'idem-key-0002'),
                  'secret_storage_capacity', 429)
            self.extensions.secret_set(ACTOR, 'a', 'z' * 100, 'idem-key-0003')  # shrink still allowed

    def test_secret_restart_durability(self):
        self.extensions.secret_set(ACTOR, 'durable.conn', 'keep-me', 'idem-key-0001')
        state2, extensions2 = make(self.directory.name)
        self.addCleanup(state2.store.close)
        self.assertEqual(extensions2.secret_resolve(ACTOR, 'durable.conn', 'internal_connector'), 'keep-me')
        metadata = extensions2.secret_metadata(ACTOR, 'durable.conn')
        self.assertEqual(metadata['name'], 'durable.conn')

    def test_wrong_key_cannot_open_state(self):
        self.extensions.secret_set(ACTOR, 'k', 'v', 'idem-key-0001')
        with self.assertRaises(ValueError) as ctx:
            make(self.directory.name, key=b'another-attacker-key-9876543210abcdef')
        self.assertEqual(ctx.exception.code, 'state_integrity_failure')  # AES-GCM key mismatch
        self.assertEqual(ctx.exception.status, 503)

    def test_tampered_ciphertext_fails_closed(self):
        self.extensions.secret_set(ACTOR, 'tamper', 'value', 'idem-key-0001')
        connection = sqlite3.connect(str(self.state.store.path))
        connection.execute('UPDATE unified_ext_secrets SET sealed=zeroblob(length(sealed))')
        connection.commit()
        connection.close()
        error(self, lambda: self.extensions.secret_resolve(ACTOR, 'tamper', 'internal_connector'),
              'state_integrity_failure', 503)

    def test_secret_limits_match_spec(self):
        self.assertEqual(ext.MAX_SECRETS_PER_ACTOR, 50)
        self.assertEqual(ext.MAX_SECRETS_GLOBAL, 500)
        self.assertEqual(ext.MAX_SECRET_VALUE_BYTES, 4096)
        self.assertEqual(ext.SECRET_PURPOSE_ALLOWLIST, frozenset(('internal_connector',)))

    def test_same_name_for_different_actors_isolated(self):
        self.extensions.secret_set(ACTOR, 'shared.name', 'mine', 'idem-key-0001')
        self.extensions.secret_set(OTHER, 'shared.name', 'theirs', 'idem-key-0001')
        self.assertEqual(self.extensions.secret_resolve(ACTOR, 'shared.name', 'internal_connector'), 'mine')
        self.assertEqual(self.extensions.secret_resolve(OTHER, 'shared.name', 'internal_connector'), 'theirs')


class Integration(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state, self.extensions = make(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_capabilities_share_one_state_without_leaking_tables(self):
        self.extensions.publish(ACTOR, 'cross.capability', {'x': 1}, 'idem-key-0001')
        self.extensions.secret_set(ACTOR, 'cross.secret', 'v', 'idem-key-0001')
        path = corpus_file(self.directory.name, [document('doc', 'teks bersama')])
        self.extensions.load_corpus(path)
        with sqlite3.connect(str(self.state.store.path)) as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn('unified_ext_events', tables)
        self.assertIn('unified_ext_secrets', tables)
        self.assertEqual(len(self.extensions.replay(ACTOR, 'cross.capability')), 1)
        self.assertEqual(self.extensions.query('teks')['results'][0]['id'], 'doc')

    def test_events_and_jobs_do_not_collide(self):
        self.state.create(ACTOR, {'messages': [{'role': 'user', 'content': 'halo'}]}, 'job-key-0001')
        event = self.extensions.publish(ACTOR, 'jobs.echo', {'n': 1}, 'idem-key-0001')
        self.assertEqual(event['sequence'], 1)
        job = self.state.get(ACTOR, self.state.create(
            ACTOR, {'messages': [{'role': 'user', 'content': 'halo'}]}, 'job-key-0001')['id'])
        self.assertEqual(job['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
