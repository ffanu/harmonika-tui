import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'runtime/tools/skill_registry.py'
spec = importlib.util.spec_from_file_location('skill_registry', SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = r.Registry()

    def test_fixed_document_routes_and_serializable_contract(self):
        descriptors = [x for x in self.registry.list() if x['id'].startswith('doc.')]
        self.assertEqual(len(descriptors), 9)
        self.assertEqual(self.registry.route('doc.read_pdf'), ('inspect', '.pdf'))
        self.assertEqual(self.registry.route('doc.create_docx'), ('create', '.docx'))
        self.assertEqual(self.registry.route('doc.edit_pdf'), ('edit', '.pdf'))
        decoded = json.loads(json.dumps(descriptors, allow_nan=False))
        for item in decoded:
            self.assertEqual(item['schema_version'], 1)
            self.assertEqual(item['execution_target'], 'local_worker')
            self.assertFalse(item['permissions']['network'])
            self.assertFalse(item['permissions']['shell'])
            self.assertEqual(item['access'], ['tui', 'internal'])
            self.assertNotIn('available', item)
        self.assertEqual(self.registry.get('doc.edit_pdf')['permissions']['filesystem'], ['read', 'write'])

    def test_registry_construction_and_listing_do_not_read_files(self):
        with patch.object(Path, 'open', side_effect=AssertionError('unexpected file access')):
            self.assertEqual(len([x for x in r.Registry().list() if x['id'].startswith('doc.')]), 9)

    def test_returned_descriptors_cannot_mutate_registry(self):
        original = self.registry.get('doc.read_pdf')
        original['input_schema']['properties']['path']['format'] = 'anything'
        original['permissions']['filesystem'].append('write')
        listing = self.registry.list()
        listing[0]['id'] = 'corrupt'
        self.assertEqual(self.registry.get('doc.read_pdf')['input_schema']['properties']['path']['format'], 'absolute-path')
        self.assertEqual(self.registry.get('doc.read_pdf')['permissions']['filesystem'], ['read'])
        self.assertNotIn('corrupt', [item['id'] for item in self.registry.list()])

    def test_unknown_skill_is_stable_and_does_not_echo_input(self):
        for value in ('shell.exec', '../secret', None, [], {}):
            with self.subTest(value=value), self.assertRaises(r.SkillError) as raised:
                self.registry.get(value)
            self.assertEqual(raised.exception.code, 'unknown_skill')
            self.assertEqual(str(raised.exception), 'The requested skill is not registered')

    def test_valid_inputs_for_each_operation(self):
        for ext in ('pdf', 'docx', 'xlsx', 'pptx'):
            self.registry.validate_input('doc.read_' + ext, {'path': '/project/FILE.' + ext.upper()})
            self.registry.validate_input('doc.create_' + ext, {'output_path': '/project/new.' + ext, 'content': {'value': ['text', 2, 1.5, None, True]}})
        self.registry.validate_input('doc.edit_pdf', {'path': '/project/a.pdf', 'output_path': '/project/b.pdf', 'changes': {'rotation': 90}})

    def test_missing_unknown_and_wrong_typed_fields(self):
        values = [None, [], {}, {'path': 2}, {'path': True},
                  {'path': '/project/a.pdf', 'principal_id': 'attacker'},
                  {'path': '/project/a.pdf', 'root': '/'},
                  {'path': 'relative.pdf'}, {'path': '/project/a\0.pdf'},
                  {'path': '/project/a.xlsx'}, {'path': '/project/a.pdf', 'transport': 'sse'}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(r.SkillError) as raised:
                self.registry.validate_input('doc.read_pdf', value)
            self.assertEqual(raised.exception.code, 'invalid_input')
        with self.assertRaises(r.SkillError):
            self.registry.validate_input('doc.create_pdf', {'output_path': '/project/a.pdf', 'content': 'not an object'})

    def test_nonfinite_and_exotic_nested_values_are_refused(self):
        for value in (float('nan'), float('inf'), float('-inf'), {1: 'bad key'}, {1, 2}, Path('/tmp/a'), b'bytes', 1 << 5000, '\ud800'):
            with self.subTest(kind=type(value).__name__), self.assertRaises(r.SkillError) as raised:
                self.registry.validate_input('doc.create_pdf', {'output_path': '/project/a.pdf', 'content': {'value': value}})
            self.assertEqual(raised.exception.code, 'invalid_input')

    def test_aggregate_utf8_byte_limit(self):
        with self.assertRaises(r.SkillError) as raised:
            self.registry.validate_input('doc.create_pdf', {'output_path': '/project/a.pdf', 'content': {'paragraphs': ['中' * (r.MAX_REQUEST_BYTES // 3)]}})
        self.assertEqual(raised.exception.code, 'input_too_large')

    def test_deep_or_cyclic_values_fail_bounded_validation(self):
        cyclic = []
        cyclic.append(cyclic)
        deep = {}
        child = deep
        for index in range(40):
            child['next'] = {}
            child = child['next']
        for value in (cyclic, deep):
            with self.assertRaises(r.SkillError) as raised:
                self.registry.validate_input('doc.create_pdf', {'output_path': '/project/a.pdf', 'content': {'value': value}})
            self.assertEqual(raised.exception.code, 'invalid_input')

    def test_many_nodes_fail_before_execution(self):
        with self.assertRaises(r.SkillError):
            self.registry.validate_input('doc.create_pdf', {'output_path': '/project/a.pdf', 'content': {'value': [None] * (r.MAX_NODES + 1)}})


if __name__ == '__main__':
    unittest.main(verbosity=2)
