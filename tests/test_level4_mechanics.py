import ast
import json
from pathlib import Path
import unittest

# Load only the pure event serializer; never initialize a lab or connect to Docker.
tree = ast.parse((Path(__file__).resolve().parents[1] / 'scripts/level4-lab/controller.py').read_text())
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fault_events')
namespace = {'json': json}
exec(compile(ast.Module(body=[function], type_ignores=[]), '<event-serializer>', 'exec'), namespace)
serialize = namespace['fault_events']

class EventMechanics(unittest.TestCase):
    def test_exec_argv_and_unknown_fields_never_exported(self):
        raw = '\n'.join(json.dumps(v) for v in [
            {'Action': 'exec_create: private-fixture-argv', 'time': 1},
            {'Action': 'exec_start: private-fixture-argv', 'time': 2},
            {'Action': 'die', 'time': 3, 'Actor': {'Attributes': {'exitCode': '42', 'private': 'private-fixture-content'}}},
        ])
        self.assertEqual(serialize(raw), [{'action': 'die', 'time': 3, 'exit_code': '42'}])
        self.assertNotIn('private-fixture', json.dumps(serialize(raw)))

    def test_invalid_timestamp_and_exit_code_are_not_copied(self):
        raw = '\n'.join(json.dumps(v) for v in [
            {'Action': 'die', 'time': 'private-fixture'},
            {'Action': 'start', 'time': 5, 'Actor': {'Attributes': {'exitCode': 'private-fixture'}}},
        ])
        self.assertEqual(serialize(raw), [{'action': 'start', 'time': 5, 'exit_code': None}])

    def test_output_is_bounded(self):
        raw = '\n'.join(json.dumps({'Action': 'start', 'time': i}) for i in range(300))
        events = serialize(raw)
        self.assertEqual(len(events), 100)
        self.assertEqual(events[0]['time'], 200)
        self.assertEqual(events[-1]['time'], 299)
