"""Focused tests for backend/debug_scanner/advanced_static.py.

Pure-stdlib bounded AST analyses: intra-function taint, per-function control
flow summaries, unreachable/constant-dead code and normalized clone detection.
All fixtures are owned; no source execution, network or host access.
"""
import importlib.util
import json
import pathlib
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0,str(_ROOT/'backend'))
from debug_scanner import advanced_static as adv

OPS = {'sast.data.flow', 'sast.control.flow', 'sast.dead.code', 'sast.duplication'}


def scan(op, path, content):
    return adv.advanced_static_scan(op, {'files': [{'path': path, 'content': content}]})


def scan_many(op, *pairs):
    return adv.advanced_static_scan(op, {'files': [{'path': p, 'content': c} for p, c in pairs]})


def rules(res):
    return [f['rule_id'] for f in res['findings']]


class TestOperationSet(unittest.TestCase):
    def test_ops_exact(self):
        self.assertEqual(adv.ADVANCED_STATIC_OPS, frozenset(OPS))
        self.assertIsInstance(adv.ADVANCED_STATIC_OPS, frozenset)

    def test_unknown_and_invalid_operations(self):
        with self.assertRaises(ValueError) as cm:
            adv.advanced_static_scan('sast.nothing', {'files': []})
        self.assertEqual(str(cm.exception), 'unknown_operation')
        with self.assertRaises(ValueError) as cm:
            adv.advanced_static_scan(None, {'files': []})
        self.assertEqual(str(cm.exception), 'invalid_operation')


class TestContract(unittest.TestCase):
    SRC = ('import os\n'
           'def run():\n'
           '    cmd = input()\n'
           '    os.system(cmd)\n')

    def test_result_contract_per_op(self):
        for op in OPS:
            with self.subTest(op=op):
                res = scan(op, 'app.py', self.SRC)
                self.assertEqual(res['status'], 'ok')
                self.assertEqual(res['operation'], op)
                self.assertEqual(res['scanned_files'], 1)
                self.assertFalse(res['source_executed'])
                self.assertFalse(res['confirmed_vulnerabilities'])
                self.assertIsInstance(res['findings'], list)
                self.assertIsInstance(res['limitations'], list)
                self.assertTrue(res['limitations'])
                self.assertFalse(res['truncated'])
                keys = [(f['evidence']['file'], f['evidence']['line'], f['rule_id']) for f in res['findings']]
                self.assertEqual(keys, sorted(keys))
                for finding in res['findings']:
                    self.assertEqual(set(finding), {'rule_id', 'severity', 'cwe', 'confidence_kind', 'evidence', 'message'})
                    self.assertEqual(set(finding['evidence']), {'file', 'line', 'sha256'})
                    self.assertIn(finding['severity'], {'critical', 'high', 'medium', 'low', 'info'})
                    self.assertIn(finding['confidence_kind'], {'proven', 'heuristic_candidate', 'rule_based_candidate'})

    def test_no_input(self):
        for op in OPS:
            res = scan_many(op)
            self.assertEqual(res['status'], 'no_input', op)
            self.assertEqual(res['scanned_files'], 0)

    def test_needs_toolchain_js_only(self):
        for op in OPS:
            res = scan(op, 'app.js', 'var x = 1;')
            self.assertEqual(res['status'], 'needs_toolchain', op)

    def test_mixed_files_skip_counted(self):
        res = scan_many('sast.dead.code', ('a.py', 'def f():\n    return 1\n'), ('b.js', 'var x;'))
        self.assertEqual(res['status'], 'ok')
        self.assertEqual(res['scanned_files'], 2)
        self.assertTrue(any('1 non-Python file(s) skipped' in l for l in res['limitations']))

    def test_error_codes(self):
        cases = [
            ('sast.dead.code', ({'nope': 1},), 'invalid_input'),
            ('sast.dead.code', ({'files': 'x'},), 'invalid_files'),
            ('sast.dead.code', ({'files': [{'path': 'a.py'}]},), 'invalid_file'),
            ('sast.dead.code', ({'files': [{'path': 'a.py', 'content': 1}]},), 'invalid_content'),
            ('sast.dead.code', ({'files': [{'path': '/etc/x.py', 'content': ''}]},), 'absolute_path'),
            ('sast.dead.code', ({'files': [{'path': 'a/../a.py', 'content': ''}]},), 'path_traversal'),
            ('sast.dead.code', ({'files': [{'path': 'id_rsa', 'content': ''}]},), 'private_source_identifier'),
            ('sast.dead.code', ({'files': [{'path': 'a.py', 'content': ''}, {'path': 'a.py', 'content': ''}]},), 'duplicate_path'),
        ]
        for op, args, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(ValueError) as cm:
                    adv.advanced_static_scan(op, *args)
                self.assertEqual(str(cm.exception), code)

    def test_too_many_files(self):
        files = [{'path': 'f%d.py' % i, 'content': ''} for i in range(21)]
        with self.assertRaises(ValueError) as cm:
            adv.advanced_static_scan('sast.dead.code', {'files': files})
        self.assertEqual(str(cm.exception), 'too_many_files')

    def test_input_too_large(self):
        with self.assertRaises(ValueError) as cm:
            scan('sast.dead.code', 'big.py', 'x = 1\n' + 'a' * 60000)
        self.assertEqual(str(cm.exception), 'input_too_large')

    def test_ast_node_limit(self):
        content = 'a = 0\n' + 'a = a + 1\n' * 5500
        with self.assertRaises(ValueError) as cm:
            scan('sast.dead.code', 'wide.py', content)
        self.assertEqual(str(cm.exception), 'ast_limit_exceeded')

    def test_syntax_error_reported(self):
        res = scan('sast.dead.code', 'broken.py', 'def broken(:\n    pass\n')
        self.assertEqual(res['status'], 'ok')
        self.assertIn('PY.SYNTAX.ERROR', rules(res))
        self.assertTrue(any('syntax' in l.lower() for l in res['limitations']))

    def test_finding_limit_and_output_bound(self):
        content = 'if False:\n    pass\n' * 320
        res = scan('sast.dead.code', 'many.py', content)
        self.assertTrue(res['truncated'])
        self.assertEqual(len(res['findings']), 300)
        self.assertTrue(any('Finding limit (300) reached' in l for l in res['limitations']))
        self.assertLessEqual(len(json.dumps(res).encode('utf-8')), 120000)

    def test_basename_collision_labels(self):
        src = 'def only():\n    acc = 0\n    for it in (1, 2):\n        acc = acc + it\n    return acc\n'
        res = scan_many('sast.duplication', ('a/util.py', src), ('b/util.py', src))
        self.assertEqual(res['status'], 'ok')
        labels = {f['evidence']['file'] for f in res['findings']}
        self.assertEqual(len(labels), 2)
        for label in labels:
            self.assertTrue(label.startswith('file_'))

    def test_no_secret_or_path_leakage(self):
        secret = 'sk-proj-SUPERSECRETTOKEN1234567890abcdef'
        src = 'KEY = "%s"\n\ndef go():\n    return eval(input())\n' % secret
        for op in OPS:
            res = scan(op, 'app.py', src)
            blob = json.dumps(res)
            self.assertNotIn(secret, blob, op)
            self.assertNotIn('app.py/Users', blob)
            self.assertNotIn('/Users/', blob)

    def test_invalid_utf8_like_paths_rejected(self):
        with self.assertRaises(ValueError) as cm:
            scan('sast.dead.code', 'a\x01b.py', 'x = 1\n')
        self.assertEqual(str(cm.exception), 'path_control_character')


class TestDataFlow(unittest.TestCase):
    def test_input_to_eval(self):
        res = scan('sast.data.flow', 'a.py', 'def e():\n    expr = input()\n    return eval(expr)\n')
        self.assertIn('ADV.PY.TAINT.EVAL_EXEC', rules(res))

    def test_request_form_to_os_system(self):
        src = ('import os\n'
               'def f():\n'
               '    cmd = request.form.get("c")\n'
               '    os.system(cmd)\n')
        res = scan('sast.data.flow', 'a.py', src)
        self.assertIn('ADV.PY.TAINT.COMMAND', rules(res))

    def test_request_args_get_to_subprocess_shell(self):
        src = ('import subprocess\n'
               'def g():\n'
               '    target = request.args["t"]\n'
               '    subprocess.run(target, shell=True)\n')
        res = scan('sast.data.flow', 'a.py', src)
        self.assertIn('ADV.PY.TAINT.COMMAND', rules(res))

    def test_request_post_to_sql(self):
        src = ('def s(q):\n'
               '    uid = request.POST["id"]\n'
               '    cur.execute("SELECT * FROM users WHERE id=" + uid)\n')
        res = scan('sast.data.flow', 'a.py', src)
        self.assertIn('ADV.PY.TAINT.SQLI', rules(res))

    def test_branch_propagation(self):
        src = ('import os\n'
               'def b():\n'
               '    d = input()\n'
               '    cmd = "echo hi"\n'
               '    if d:\n'
               '        cmd = d\n'
               '    os.system(cmd)\n')
        res = scan('sast.data.flow', 'a.py', src)
        self.assertIn('ADV.PY.TAINT.COMMAND', rules(res))

    def test_module_scope_flow(self):
        res = scan('sast.data.flow', 'm.py', 'import os\ndata = input()\nos.system(data)\n')
        self.assertIn('ADV.PY.TAINT.COMMAND', rules(res))

    def test_no_cross_function_leakage(self):
        src = ('import os\n'
               'def source():\n'
               '    x = input()\n'
               '    return x\n'
               'def sink():\n'
               '    os.system(x)\n')
        res = scan('sast.data.flow', 'a.py', src)
        self.assertEqual(rules(res), [])

    def test_rebinding_clears_taint(self):
        src = ('import os\n'
               'def r():\n'
               '    q = input()\n'
               '    q = "echo ok"\n'
               '    os.system(q)\n')
        res = scan('sast.data.flow', 'a.py', src)
        self.assertEqual(rules(res), [])

    def test_parameterized_sql_safe(self):
        src = ('def q(v):\n'
               '    cur.execute("SELECT * FROM t WHERE id=?", (v,))\n'
               'def q2():\n'
               '    v = input()\n'
               '    cur.execute("SELECT * FROM t WHERE id=?", (v,))\n')
        res = scan('sast.data.flow', 'a.py', src)
        self.assertEqual(rules(res), [])
        self.assertTrue(any('Bound SQL parameters' in l for l in res['limitations']))

    def test_literal_eval_safe(self):
        res = scan('sast.data.flow', 'a.py', 'def n():\n    return eval("1+1")\n')
        self.assertEqual(rules(res), [])

    def test_sanitizer_uncertainty_explicit(self):
        src = 'def s():\n    v = int(input())\n    return eval(v)\n'
        res = scan('sast.data.flow', 'a.py', src)
        self.assertIn('ADV.PY.TAINT.UNVERIFIED_TRANSFORM', rules(res))
        self.assertNotIn('ADV.PY.TAINT.EVAL_EXEC', rules(res))
        self.assertTrue(any('unverified' in l and 'not proof of safety' in l for l in res['limitations']))

    def test_unknown_name_sink_safe(self):
        res = scan('sast.data.flow', 'a.py', 'import os\ndef u(y):\n    os.system(y)\n')
        self.assertEqual(rules(res), [])

    def test_summary_shape(self):
        res = scan('sast.data.flow', 'a.py', 'def a():\n    return 1\n')
        summary = res['summary']
        self.assertEqual(summary['function_scopes'], 1)
        self.assertGreaterEqual(summary['scopes_analyzed'], 2)
        self.assertIsInstance(summary['sinks_inspected'], int)


class TestControlFlow(unittest.TestCase):
    SRC = ('def branchy(a, b):\n'
           '    if a:\n'
           '        return 1\n'
           '    for i in range(3):\n'
           '        if b:\n'
           '            break\n'
           '        continue\n'
           '    raise ValueError("x")\n')

    def _entry(self, res):
        fns = res['summary']['functions']
        self.assertEqual(len(fns), 1)
        return fns[0]

    def test_counters(self):
        e = self._entry(scan('sast.control.flow', 'a.py', self.SRC))
        self.assertEqual(e['function'], 'branchy')
        self.assertEqual(e['returns'], 1)
        self.assertEqual(e['raises'], 1)
        self.assertEqual(e['breaks'], 1)
        self.assertEqual(e['continues'], 1)
        self.assertEqual(e['loops'], 1)
        self.assertEqual(e['branch_points'], 2)

    def test_blocks_and_edges_consistent(self):
        e = self._entry(scan('sast.control.flow', 'a.py', self.SRC))
        self.assertGreaterEqual(e['blocks'], 1)
        self.assertGreaterEqual(e['edges'], e['blocks'] - 1)

    def test_nested_functions_separate(self):
        src = ('def outer():\n'
               '    def inner(x):\n'
               '        if x:\n'
               '            return 1\n'
               '        return 2\n'
               '    return inner\n')
        res = scan('sast.control.flow', 'a.py', src)
        fns = res['summary']['functions']
        self.assertEqual({f['function'] for f in fns}, {'outer', 'inner'})
        inner = next(f for f in fns if f['function'] == 'inner')
        self.assertEqual(inner['returns'], 2)
        self.assertEqual(inner['branch_points'], 1)

    def test_try_approximation_disclosed(self):
        src = ('def t():\n'
               '    try:\n'
               '        return 1\n'
               '    except ValueError:\n'
               '        return 2\n')
        res = scan('sast.control.flow', 'a.py', src)
        self.assertTrue(any('approximated' in l for l in res['limitations']))
        self.assertTrue(any('symbolic execution' in l for l in res['limitations']))

    def test_entry_cap(self):
        src = ''.join('def f%d():\n    return %d\n' % (i, i) for i in range(250))
        res = scan('sast.control.flow', 'a.py', src)
        self.assertEqual(len(res['summary']['functions']), 200)
        self.assertTrue(any('capped at 200' in l for l in res['limitations']))


class TestDeadCode(unittest.TestCase):
    def test_after_return(self):
        res = scan('sast.dead.code', 'a.py', 'def f():\n    return 1\n    print("x")\n    y = 2\n')
        self.assertEqual(rules(res), ['ADV.PY.DEAD.UNREACHABLE'])
        f = res['findings'][0]
        self.assertEqual(f['evidence']['line'], 3)
        self.assertIn('2 statement(s)', f['message'])

    def test_after_raise(self):
        res = scan('sast.dead.code', 'a.py', 'def f():\n    raise ValueError()\n    return 1\n')
        self.assertIn('ADV.PY.DEAD.UNREACHABLE', rules(res))

    def test_after_break_and_continue(self):
        src = ('def k(xs):\n'
               '    for i in xs:\n'
               '        continue\n'
               '        print("d1")\n'
               'def m(xs):\n'
               '    while xs:\n'
               '        break\n'
               '        print("d2")\n')
        res = scan('sast.dead.code', 'a.py', src)
        self.assertEqual(rules(res).count('ADV.PY.DEAD.UNREACHABLE'), 2)

    def test_constant_false_if(self):
        res = scan('sast.dead.code', 'a.py', 'def f():\n    if False:\n        return 1\n    return 2\n')
        self.assertEqual(rules(res), ['ADV.PY.DEAD.CONST_BRANCH'])
        self.assertEqual(res['findings'][0]['cwe'], 570)

    def test_while_false(self):
        res = scan('sast.dead.code', 'a.py', 'def f():\n    while False:\n        print("never")\n    return 1\n')
        self.assertIn('ADV.PY.DEAD.CONST_BRANCH', rules(res))

    def test_while_false_else_runs(self):
        src = ('def f():\n'
               '    while False:\n'
               '        print("never")\n'
               '    else:\n'
               '        return 1\n')
        res = scan('sast.dead.code', 'a.py', src)
        got = rules(res)
        self.assertEqual(got.count('ADV.PY.DEAD.CONST_BRANCH'), 1)
        self.assertNotIn('ADV.PY.DEAD.UNREACHABLE', got)

    def test_constant_true_else_dead(self):
        src = ('def f():\n'
               '    if True:\n'
               '        a = 1\n'
               '    else:\n'
               '        a = 2\n'
               '    return a\n')
        res = scan('sast.dead.code', 'a.py', src)
        self.assertEqual(rules(res), ['ADV.PY.DEAD.CONST_BRANCH'])
        self.assertEqual(res['findings'][0]['evidence']['line'], 5)
        self.assertEqual(res['findings'][0]['cwe'], 571)

    def test_fully_terminating_if_else(self):
        src = ('def f(x):\n'
               '    if x:\n'
               '        return 1\n'
               '    else:\n'
               '        return 2\n'
               '    print("dead")\n')
        res = scan('sast.dead.code', 'a.py', src)
        self.assertIn('ADV.PY.DEAD.UNREACHABLE', rules(res))
        self.assertEqual(res['findings'][0]['evidence']['line'], 6)

    def test_reachable_else_return_not_flagged(self):
        src = ('def f(x):\n'
               '    if x:\n'
               '        return 1\n'
               '    return 2\n')
        res = scan('sast.dead.code', 'a.py', src)
        self.assertEqual(rules(res), [])

    def test_elif_after_const_false(self):
        src = ('def f(y):\n'
               '    if False:\n'
               '        x = 1\n'
               '    elif y:\n'
               '        x = 2\n'
               '    return x\n')
        res = scan('sast.dead.code', 'a.py', src)
        self.assertEqual(rules(res), ['ADV.PY.DEAD.CONST_BRANCH'])

    def test_nested_function_scanned_separately(self):
        src = ('def outer():\n'
               '    def inner():\n'
               '        return 1\n'
               '        print("dead-inner")\n'
               '    return inner\n')
        res = scan('sast.dead.code', 'a.py', src)
        self.assertEqual(rules(res), ['ADV.PY.DEAD.UNREACHABLE'])
        self.assertEqual(res['findings'][0]['evidence']['line'], 4)

    def test_module_level_dead_code(self):
        res = scan('sast.dead.code', 'a.py', 'raise SystemExit\nx = 1\n')
        self.assertIn('ADV.PY.DEAD.UNREACHABLE', rules(res))

    def test_summary_and_limitations(self):
        res = scan('sast.dead.code', 'a.py', 'def f():\n    return 1\n')
        self.assertEqual(res['summary']['scopes_scanned'], 2)
        self.assertTrue(any('No source is modified' in l for l in res['limitations']))


class TestDuplication(unittest.TestCase):
    CLONE_A = ('def calc_total(items):\n'
               '    acc = 0\n'
               '    for it in items:\n'
               '        acc = acc + it\n'
               '    return acc\n')
    CLONE_B = ('def sum_values(entries):\n'
               '    out = 0\n'
               '    for e in entries:\n'
               '        out = out + e\n'
               '    return out\n')

    def test_clone_detected_with_refs_only(self):
        res = scan('sast.duplication', 'a.py', self.CLONE_A + '\n' + self.CLONE_B)
        got = rules(res)
        self.assertEqual(got.count('ADV.PY.DUPLICATE.FUNCTION'), 2)
        messages = [f['message'] for f in res['findings']]
        self.assertTrue(any('a.py:1' in m for m in messages))
        self.assertTrue(any('a.py:7' in m for m in messages))
        for m in messages:
            self.assertNotIn('acc', m)
            self.assertNotIn('calc_total', m)
        self.assertEqual(res['summary']['clone_groups'], 1)

    def test_differing_constants_not_clones(self):
        src = ('def double(x):\n'
               '    return x * 2\n'
               'def triple(y):\n'
               '    return y * 3\n')
        res = scan('sast.duplication', 'a.py', src)
        self.assertEqual(rules(res), [])

    def test_different_structure_not_clones(self):
        src = ('def one(x):\n'
               '    return x + 1\n'
               'def two(x):\n'
               '    return 1 + x\n')
        res = scan('sast.duplication', 'a.py', src)
        self.assertEqual(rules(res), [])

    def test_cross_file_clone(self):
        res = scan_many('sast.duplication', ('a.py', self.CLONE_A), ('b.py', self.CLONE_B))
        self.assertEqual(rules(res).count('ADV.PY.DUPLICATE.FUNCTION'), 2)
        labels = {f['evidence']['file'] for f in res['findings']}
        self.assertEqual(labels, {'a.py', 'b.py'})

    def test_trivial_functions_skipped(self):
        res = scan('sast.duplication', 'a.py', 'def a():\n    pass\n\ndef b():\n    pass\n')
        self.assertEqual(rules(res), [])

    def test_literals_preserved_in_normalization(self):
        src = ('def greet_a(n):\n'
               '    m = "hello"\n'
               '    return m + n\n'
               'def greet_b(k):\n'
               '    m = "goodbye"\n'
               '    return m + k\n')
        res = scan('sast.duplication', 'a.py', src)
        self.assertEqual(rules(res), [])

    def test_summary_shape(self):
        res = scan('sast.duplication', 'a.py', self.CLONE_A + '\n' + self.CLONE_B)
        summary = res['summary']
        self.assertEqual(summary['functions_compared'], 2)
        self.assertEqual(len(summary['groups']), 1)
        self.assertEqual(summary['groups'][0]['size'], 2)


if __name__ == '__main__':
    unittest.main()
