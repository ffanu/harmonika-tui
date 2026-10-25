"""Root integration regressions: secret rotation/delete idempotency, durable event
sequence, strict corpus sources, global ciphertext bound on update.

These tests assert the CORRECT completed contract. Failures against the current
worktree are expected bug proof while the root integration fix lands; they must
never be weakened to fake a pass.
"""
import hashlib
import json
import sqlite3
import sys
import tempfile
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
KEY_A = 'rotate-key-aaaa'
KEY_B = 'rotate-key-bbbb'
KEY_DEL = 'delete-key-dddd'
KEY_NEW = 'recreate-key-nnnn'


def make(directory, name='regress.db', key=b'unit-test-regression-key-012345678'):
    state = State(str(Path(directory) / name), key)
    return state, Extensions(state)


def expect_error(test, fn, code=None, status=400):
    with test.assertRaises(ValueError) as ctx:
        fn()
    if code is not None:
        test.assertEqual(ctx.exception.code, code)
    test.assertEqual(ctx.exception.status, status)


def document(identifier, text, **override):
    entry = {'id': identifier, 'title': 'Dok ' + identifier, 'text': text,
             'source': 'https://hai.harmonika.id/docs/' + identifier,
             'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
             'verified_at': '2026-01-15'}
    entry.update(override)
    return entry


def corpus_file(directory, documents, name='corpus.json', raw=None):
    path = Path(directory) / name
    path.write_text(raw if raw is not None else json.dumps({'documents': documents}),
                    encoding='utf-8')
    return path


class SecretRotationRegressions(unittest.TestCase):
    def test_rotate_then_old_key_replays_without_rollback_and_conflicts_on_new_value(self):
        with tempfile.TemporaryDirectory() as directory:
            state, extensions = make(directory)
            first = extensions.secret_set(ACTOR, 'svc.token', 'value-A', KEY_A)
            self.assertFalse(first['replayed'])
            rotated = extensions.secret_set(ACTOR, 'svc.token', 'value-B', KEY_B)
            self.assertFalse(rotated['replayed'])
            # Retrying the OLD key with its original payload must replay stored
            # metadata, never rewrite the row or roll back the rotated value.
            retried = extensions.secret_set(ACTOR, 'svc.token', 'value-A', KEY_A)
            self.assertTrue(retried['replayed'])
            self.assertEqual(retried['created_at'], first['created_at'])
            self.assertEqual(retried['updated_at'], first['updated_at'])  # replay original committed response
            self.assertEqual(extensions.secret_metadata(ACTOR,'svc.token')['updated_at'],rotated['updated_at'])
            self.assertEqual(extensions.secret_resolve(ACTOR, 'svc.token', 'internal_connector'),
                             'value-B')
            # The old key is bound to its committed payload forever.
            expect_error(self, lambda: extensions.secret_set(ACTOR, 'svc.token', 'value-C', KEY_A),
                         code='idempotency_conflict', status=409)
            self.assertEqual(extensions.secret_resolve(ACTOR, 'svc.token', 'internal_connector'),
                             'value-B')


class SecretDeleteRegressions(unittest.TestCase):
    def test_delete_retry_idempotent_and_never_deletes_recreated_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            state, extensions = make(directory)
            extensions.secret_set(ACTOR, 'svc.token', 'v1', 'seed-key-0001')
            first = extensions.secret_delete(ACTOR, 'svc.token', KEY_DEL)
            self.assertTrue(first['deleted'])
            # Retry of the same delete key must replay, not 404.
            retry = extensions.secret_delete(ACTOR, 'svc.token', KEY_DEL)
            self.assertTrue(retry['deleted'])
            self.assertTrue(retry['replayed'])
            # Operator recreates the secret under a NEW idempotency key.
            extensions.secret_set(ACTOR, 'svc.token', 'v2-new', KEY_NEW)
            # The old delete key must never remove the recreated secret.
            stale = extensions.secret_delete(ACTOR, 'svc.token', KEY_DEL)
            self.assertTrue(stale['replayed'])
            self.assertEqual(extensions.secret_resolve(ACTOR, 'svc.token', 'internal_connector'),
                             'v2-new')
            metadata = extensions.secret_metadata(ACTOR, 'svc.token')
            self.assertEqual(metadata['name'], 'svc.token')


class EventSequenceDurability(unittest.TestCase):
    def test_sequence_monotonic_after_full_expiry_prune_and_old_cursor_finds_new_event(self):
        with tempfile.TemporaryDirectory() as directory:
            state, extensions = make(directory)
            first = extensions.publish(ACTOR, 'stream.topic', {'n': 1}, 'event-key-0001')
            second = extensions.publish(ACTOR, 'stream.topic', {'n': 2}, 'event-key-0002')
            self.assertEqual((first['sequence'], second['sequence']), (1, 2))
            # Force every stored event to expire, then prune via replay.
            connection = sqlite3.connect(str(state.store.path))
            connection.execute('UPDATE unified_ext_events SET expires=1')
            connection.commit()
            connection.close()
            self.assertEqual(extensions.replay(ACTOR, 'stream.topic'), [])
            # The per actor+topic counter must be durable across pruning: a new
            # event continues after the highest ever issued sequence.
            third = extensions.publish(ACTOR, 'stream.topic', {'n': 3}, 'event-key-0003')
            self.assertEqual(third['sequence'], 3)
            # A cursor taken before the prune still finds the newer sequence.
            cursor = extensions.replay(ACTOR, 'stream.topic', after_sequence=2)
            self.assertEqual([event['sequence'] for event in cursor], [3])
            self.assertEqual(cursor[0]['payload'], {'n': 3})


class CorpusSourceRegressions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state, self.extensions = make(self.directory.name, 'corpus.db')

    def test_rejects_source_without_http_scheme(self):
        for source in ('operator/notes.txt', 'file:///srv/docs/x.md', 'ftp://files.example.com/x'):
            entry = document('s', 'teks sumber', source=source)
            expect_error(self, lambda entry=entry, source=source: self.extensions.load_corpus(
                corpus_file(self.directory.name, [entry], source.replace('/', '_') + '.json')),
                status=400)

    def test_rejects_userinfo_credentials_in_source(self):
        for source in ('https://alice:hunter2@hai.harmonika.id/docs/x',
                       'http://token@hai.harmonika.id/docs/y'):
            entry = document('u', 'teks kredensial', source=source)
            expect_error(self, lambda entry=entry: self.extensions.load_corpus(
                corpus_file(self.directory.name, [entry], 'userinfo.json')), status=400)

    def test_rejects_hash_mismatch(self):
        entry = document('h', 'teks hash', sha256='0' * 64)
        expect_error(self, lambda: self.extensions.load_corpus(
            corpus_file(self.directory.name, [entry], 'hash.json')),
            code='corpus_hash_mismatch', status=400)

    def test_rejects_duplicate_json_fields(self):
        text_one, text_two = 'versi pertama teks', 'versi kedua teks berbeda'
        raw = ('{"documents":[{"id":"dupfield","title":"T","text":' + json.dumps(text_one)
               + ',"text":' + json.dumps(text_two)
               + ',"source":"https://hai.harmonika.id/docs/dup",'
               + '"sha256":' + json.dumps(hashlib.sha256(text_two.encode('utf-8')).hexdigest())
               + ',"verified_at":"2026-01-15"}]}')
        # A duplicate member must be rejected outright; last-key-wins parsing is
        # not acceptable for a verified corpus even when the surviving text hashes correctly.
        expect_error(self, lambda: self.extensions.load_corpus(
            corpus_file(self.directory.name, None, 'dup.json', raw=raw)), status=400)
        expect_error(self, lambda: self.extensions.query('versi'), code='corpus_unavailable', status=503)


class SecretCiphertextBoundRegressions(unittest.TestCase):
    def test_global_ciphertext_bound_enforced_on_update_too(self):
        with tempfile.TemporaryDirectory() as directory:
            state, extensions = make(directory)
            extensions.secret_set(ACTOR, 'a.key', 'v' * 10, 'grow-key-0001')
            extensions.secret_set(OTHER, 'b.key', 'v' * 10, 'grow-key-0002')
            with sqlite3.connect(str(state.store.path)) as db:
                total = db.execute('SELECT COALESCE(SUM(length(sealed)),0) '
                                   'FROM unified_ext_secrets').fetchone()[0]
            # Replacing one secret with a much larger value must respect the
            # GLOBAL ciphertext bound, not only the per-actor bound.
            with patch.object(ext, 'MAX_SECRET_GLOBAL_CIPHERTEXT', total + 128):
                expect_error(self, lambda: extensions.secret_set(ACTOR, 'a.key', 'x' * 4000,
                                                                 'grow-key-0003'),
                             code='secret_storage_capacity', status=429)
            # No partial write may survive the rejected update.
            self.assertEqual(extensions.secret_resolve(ACTOR, 'a.key', 'internal_connector'),
                             'v' * 10)


if __name__ == '__main__':
    unittest.main()
