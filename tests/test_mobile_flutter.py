"""Source-contract checks; these do not claim a Flutter build or device test."""
import ast
import json
from pathlib import Path
import re
import sqlite3
import unittest

from backend.mobile_engine import flutter_modules as modules


IDS = {
    'state.setup.riverpod', 'state.setup.bloc', 'local.sqlite', 'local.hive',
    'local.migration', 'local.conflict.resolve', 'local.sync.engine',
}


def source(skill_id):
    return modules.dispatch(skill_id, {})['files'][0]['content']


class ModuleContracts(unittest.TestCase):
    def test_all_ids_produce_bounded_source_and_integration_notes(self):
        self.assertEqual(set(modules.descriptors()), IDS)
        for skill_id in IDS:
            with self.subTest(skill_id=skill_id):
                out = modules.dispatch(skill_id, {})
                self.assertEqual(out['status'], 'completed')
                self.assertEqual(out['mode'], 'source_generation')
                self.assertIs(out['runtime_verified'], False)
                self.assertIs(out['external_actions_performed'], False)
                self.assertTrue(out['warnings'])
                self.assertLess(len(json.dumps(out).encode()), 262144)
                files = out['files']
                self.assertEqual(len(files), 2)
                self.assertEqual(len({f['path'] for f in files}), 2)
                self.assertTrue(files[0]['path'].endswith('.dart'))
                self.assertIn('not compiled', files[0]['content'])
                self.assertIn('No runtime result is asserted', files[1]['content'])
                self.assertNotIn('TODO', files[0]['content'])
                self.assertNotIn('UnimplementedError', files[0]['content'])
                for f in files:
                    self.assertLess(len(f['content'].encode()), 65536)
                    self.assertNotIn('..', f['path'].split('/'))

    def test_strict_objects_and_all_unknown_fields(self):
        for skill_id in IDS:
            for payload in (None, [], '', {'spec': 'read a host file'},
                            {'actor': 'admin'}, {'path': '/etc/passwd'},
                            {'name': 'Valid', 'secret': 'not-accepted'}):
                with self.subTest(skill_id=skill_id, payload=payload):
                    with self.assertRaises(ValueError):
                        modules.dispatch(skill_id, payload)

    def test_framework_is_explicitly_flutter_only(self):
        for skill_id in IDS:
            self.assertEqual(modules.dispatch(skill_id, {'framework': 'flutter'})['status'], 'completed')
            for framework in ('expo', 'react_native', 'kotlin', 'swift', '', None, [], 1):
                with self.assertRaises(ValueError):
                    modules.dispatch(skill_id, {'framework': framework})

    def test_names_identifiers_and_injection_are_rejected(self):
        bad_names = ['a' * 65, '', ' Name', 'Name\n', "Name'; exec()", '${bad}',
                     'Name\\path', '<script>', '@@NAME@@', 'A`command`', 'A\x00B']
        bad_identifiers = ['', 'a', 'id.a_b.app', 'id.app\n', 'id.app/path',
                           'id.app;run', 'id.${value}.app', 'id.' + 'a' * 100, 123, None]
        for value in bad_names:
            with self.assertRaises(ValueError):
                modules.dispatch('local.sqlite', {'name': value})
        for value in bad_identifiers:
            with self.assertRaises(ValueError):
                modules.dispatch('local.sqlite', {'identifier': value})

    def test_metadata_does_not_get_interpolated_into_dart(self):
        for skill_id in IDS:
            before = modules.dispatch(skill_id, {})
            after = modules.dispatch(skill_id, {'name': 'Safe Project-12',
                'identifier': 'com.example.project', 'framework': 'flutter'})
            self.assertEqual(before['files'], after['files'])

    def test_unknown_id_does_not_silently_fall_back(self):
        for skill_id in ('net.setup.graphql', 'state.setup.unknown', 'local.sqlite.read', None, []):
            with self.assertRaises(ValueError):
                modules.dispatch(skill_id, {})

    def test_descriptors_do_not_share_mutable_schema(self):
        descriptors = modules.descriptors()
        descriptors['local.sqlite']['input_schema']['properties'].clear()
        self.assertIn('framework', modules.descriptors()['local.sqlite']['input_schema']['properties'])
        self.assertIn('framework', descriptors['local.hive']['input_schema']['properties'])

    def test_generator_python_cannot_open_files_network_or_processes(self):
        tree = ast.parse(Path(modules.__file__).read_text())
        imports = {n.names[0].name for n in tree.body if isinstance(n, ast.Import)}
        self.assertEqual(imports, {'copy', 're'})
        forbidden = {'open', 'exec', 'eval', '__import__', 'compile'}
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                             and n.func.id in forbidden for n in ast.walk(tree)))


class StorageContracts(unittest.TestCase):
    def test_sqlite_create_and_v1_upgrade_agree_without_data_loss(self):
        sql = re.findall(r"execute\('([^']+)'\)", source('local.sqlite'))
        self.assertEqual(len(sql), 2)
        fresh = sqlite3.connect(':memory:')
        upgraded = sqlite3.connect(':memory:')
        self.addCleanup(fresh.close)
        self.addCleanup(upgraded.close)
        fresh.execute(sql[0])
        upgraded.execute('CREATE TABLE records (id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL)')
        upgraded.execute('INSERT INTO records VALUES (?, ?, ?)', ('existing', '{"value":1}', 7))
        upgraded.execute(sql[1])
        self.assertEqual(fresh.execute('PRAGMA table_info(records)').fetchall(),
                         upgraded.execute('PRAGMA table_info(records)').fetchall())
        self.assertEqual(upgraded.execute('SELECT * FROM records').fetchone(),
                         ('existing', '{"value":1}', 7, 0))

    def test_revision_predicate_and_parameterized_ids_protect_existing_record(self):
        code = source('local.sqlite')
        self.assertIn("where: 'id = ? AND revision = ?'", code)
        self.assertIn('current != expectedRevision', code)
        self.assertIn('ConflictAlgorithm.abort', code)
        self.assertNotIn('ConflictAlgorithm.replace', code)
        self.assertIn('_db.transaction', code)
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute(re.findall(r"execute\('([^']+)'\)", code)[0])
        hostile_id = "x'); DROP TABLE records; --"
        db.execute('INSERT INTO records VALUES (?, ?, ?, ?)', (hostile_id, '{}', 2, 0))
        cur = db.execute('UPDATE records SET payload = ? WHERE id = ? AND revision = ?',
                         ('{"changed":true}', hostile_id, 1))
        self.assertEqual(cur.rowcount, 0)
        self.assertEqual(db.execute('SELECT revision,payload FROM records WHERE id = ?',
                                   (hostile_id,)).fetchone(), (2, '{}'))

    def test_migration_plan_has_real_contiguous_versioned_sql(self):
        code = source('local.migration')
        self.assertIn('migration_steps_must_be_contiguous', code)
        self.assertIn('schema_downgrade_refused', code)
        self.assertIn('oldVersion + 1', code)
        self.assertNotIn('.transaction(', code)  # Opening callbacks already own one.
        sql = re.findall(r"execute\('([^']+)'\)", code)
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        for statement in sql:
            db.execute(statement)
        db.execute('INSERT INTO migration_records(id,payload) VALUES (?,?)', ('r', '{}'))
        self.assertEqual(db.execute('SELECT revision FROM migration_records').fetchone(), (1,))

    def test_outbox_retains_conflicts_and_requires_idempotent_remote_ack(self):
        code = source('local.sync.engine')
        for contract in ('idempotency_key_reused_with_different_input',
                         'reply.requestId != mutation.requestId',
                         "{'status': 'conflict'}", "whereArgs: [requestId, 'conflict']",
                         'reply.revision! <= mutation.baseRevision',
                         '_draining ??=', 'count < 50', 'await _draining'):
            self.assertIn(contract, code)
        # Compile the actual escaped Dart SQL literal into SQLite for constraints.
        literal = re.search(r"execute\('((?:\\.|[^'\\])*)'\)", code)[1]
        statement = literal.replace("\\'", "'")
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute(statement)
        db.execute('INSERT INTO outbox(request_id,record_id,base_revision,payload) VALUES (?,?,?,?)',
                   ('req-1', 'record', 1, '{}'))
        self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(), ('pending',))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute('INSERT INTO outbox(request_id,record_id,base_revision,payload) VALUES (?,?,?,?)',
                       ('req-2', 'record', 1, '{}'))
        db.execute('UPDATE outbox SET status = ? WHERE request_id = ?', ('conflict', 'req-1'))
        self.assertEqual(db.execute('SELECT request_id FROM outbox WHERE status = ?',
                                   ('conflict',)).fetchall(), [('req-1',)])

    def test_merge_is_three_way_and_never_returns_partial_conflicting_write(self):
        code = source('local.conflict.resolve')
        self.assertIn("base == null) return MergeResult(null, const ['unknown_base'], false)", code)
        self.assertIn('x.containsKey(key) == y.containsKey(key)', code)
        self.assertIn('equal(l, b)', code)
        self.assertIn('equal(r, b)', code)
        self.assertIn('conflicts.isEmpty ? merged : null', code)
        self.assertIn('jsonDecode(encoded)', code)

    def test_state_owners_dispose_and_ignore_late_async_results(self):
        riverpod = source('state.setup.riverpod')
        bloc = source('state.setup.bloc')
        self.assertIn('ref.onDispose', riverpod)
        self.assertIn('_disposed || generation != _generation', riverpod)
        self.assertIn('isClosed || generation != _generation', bloc)
        self.assertIn('return super.close()', bloc)
        self.assertNotIn('error.toString()', riverpod + bloc)
        hive = source('local.hive')
        self.assertIn('await _box.close()', hive)
        self.assertIn('jsonEncode(value)', hive)
        self.assertNotIn('Hive.close()', hive)


if __name__ == '__main__':
    unittest.main()
