"""Tests for the Harmonika engineering analyzers.

The analyzers depend on engineering_evidence.prepare, which another worker owns.
This module uses the real implementation when one is reachable (the integrated
runtime copy, an explicit HARMONIKA_ENGINEERING_EVIDENCE path, or the sibling
worktree, read only). When none is reachable, or when
HARMONIKA_ENGINEERING_EVIDENCE=stub forces it, these tests run against the stub
fixture below, which mocks the documented prepare contract so they stay
standalone. The run prints which source was used so results are never ambiguous.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
ANALYZERS = ROOT / 'skills/harmonika-engineering/scripts/engineering_analyzers.py'
CANDIDATES = [ROOT / 'runtime/tools/engineering_evidence.py',
              Path(os.environ.get('HARMONIKA_ENGINEERING_EVIDENCE', '/nonexistent')),
              ROOT.parent / 'harmonika-engineering-opencode/runtime/tools/engineering_evidence.py']


def stub_module():
    """Minimal fixture for the prepare contract: redact, bound, number evidence."""
    module = types.ModuleType('engineering_evidence')
    secret = re.compile(r'''(?im)(\b\w*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)\w*\b["']?\s*[:=]\s*)(?:["'][^"'\r\n]*["']|[^\s,;\r\n]+)''')

    def redact(text):
        if not isinstance(text, str):
            raise ValueError('invalid_text')
        return secret.sub(r'\1[REDACTED]', text)

    def text(value, limit, required=False):
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
            raise ValueError('invalid_text')
        return value

    def prepare(payload):
        if not isinstance(payload, dict) or set(payload) - {'request', 'context', 'files', 'diff', 'logs'} or 'request' not in payload:
            raise ValueError('invalid_input')
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 131072:
            raise ValueError('input_too_large')
        result = {'request': redact(text(payload['request'], 8000, True)), 'context': redact(text(payload.get('context', ''), 12000)),
                  'diff': redact(text(payload.get('diff', ''), 64000)), 'logs': redact(text(payload.get('logs', ''), 32000)), 'files': []}
        files = payload.get('files', [])
        if not isinstance(files, list) or len(files) > 8:
            raise ValueError('invalid_files')
        for item in files:
            path = text(item['path'], 240, True)
            if path.startswith('/') or '..' in path.split('/') or Path(path).name in {'.env', 'id_rsa'}:
                raise ValueError('invalid_source_identifier')
            result['files'].append({'path': path, 'content': redact(text(item['content'], 32000)), 'language': text(item.get('language', ''), 32)})
        result['redaction_count'] = json.dumps(result, ensure_ascii=False).count('[REDACTED')
        result['warnings'] = ['Sensitive values redacted before analysis'] if result['redaction_count'] else []
        result['truncated'] = False
        result['evidence'] = []

        def collect(source, path, body):
            for number, line in enumerate(body.splitlines(), 1):
                if not line.strip():
                    continue
                if len(result['evidence']) >= 200:
                    result['truncated'] = True
                    return
                result['evidence'].append({'id': 'e%d' % (len(result['evidence']) + 1), 'source': source,
                                           'path': path, 'line': number, 'text': line[:240]})
        for item in result['files']:
            collect('file', item['path'], item['content'])
        collect('diff', 'supplied.diff', result['diff'])
        collect('logs', 'supplied.log', result['logs'])
        return result

    module.prepare = prepare
    module.redact = redact
    return module, 'stub fixture in tests/test_engineering_analyzers.py'


def load_evidence():
    if os.environ.get('HARMONIKA_ENGINEERING_EVIDENCE') == 'stub':
        return stub_module()
    for candidate in CANDIDATES:
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location('engineering_evidence', candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module, str(candidate)
    return stub_module()


EVIDENCE, EVIDENCE_SOURCE = load_evidence()
sys.modules['engineering_evidence'] = EVIDENCE
SPEC = importlib.util.spec_from_file_location('engineering_analyzers', ANALYZERS)
analyzers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyzers)

VULNERABLE = '''import os, subprocess, pickle, requests

private_key = "MIIBOgIBAAJBAKj34Gk"

def get_user(conn, user_id):
    cursor = conn.cursor()
    cursor.execute(f"SELECT name FROM users WHERE id = {user_id}")
    return cursor.fetchone()

def enrich(orders, session):
    result = []
    for order in orders:
        customer = session.query(Customer).filter(Customer.id == order.customer_id).first()
        result.append(customer)
    return result

def unpack(request):
    subprocess.run("tar -xf " + request.args["name"], shell=True)
    blob = pickle.loads(request.data)
    try:
        return blob["value"]
    except:
        pass

def tally(items=[]):
    return sum(items) / len(items)
'''
SAFE = '''"""Order helpers."""
import os


def get_user(conn, user_id):
    """Return one user row, or None."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT name FROM users WHERE id = %s LIMIT 1", (user_id,))
        return cursor.fetchone()


def enrich(orders, session):
    """Return the customers for every order in one query."""
    identifiers = {order.customer_id for order in orders}
    return session.query(Customer).filter(Customer.id.in_(identifiers)).all()


def parse_limit(value: str) -> int:
    """Return a bounded page size."""
    if not value:
        raise ValueError("limit is required")
    return min(int(value), 100)
'''
ROUTED = '''"""Order API."""
import os
from flask import Flask
import requests

app = Flask(__name__)
TIMEOUT = int(os.environ.get("ORDER_TIMEOUT", "5"))


@app.route("/orders/<order_id>", methods=["GET"])
def get_order(order_id):
    """Return one order."""
    if not order_id:
        raise ValueError("order_id is required")
    return fetch(order_id)


def fetch(order_id, limit):
    return requests.get(os.environ["UPSTREAM"], timeout=TIMEOUT).json()
'''
TRACEBACK_LOG = '''2026-10-24T03:11:02Z INFO  worker started
2026-10-24T03:11:44Z ERROR Traceback (most recent call last):
  File "app/service.py", line 7, in get_user
    cursor.execute(f"SELECT name FROM users WHERE id = {user_id}")
KeyError: 'user_id'
2026-10-24T03:12:02Z ERROR "GET /api/users HTTP/1.1" 500 12
'''
DIFF = '''diff --git a/app/web.py b/app/web.py
--- a/app/web.py
+++ b/app/web.py
@@ -10,5 +10,8 @@ def handler(request):
     name = request.args.get("name")
     legacy = "SELECT * FROM users WHERE name = '" + name + "'"
+    query = "DELETE FROM sessions WHERE name = '" + name + "'"
+    node.innerHTML = name
+    return render(query)
'''


def review(**payload):
    payload.setdefault('request', 'Review this change before merge.')
    return analyzers.analyze('code_review', payload)


def rules(result):
    return [item['rule'] for item in result['findings']]


class Envelope(unittest.TestCase):
    def test_contract_shape_for_every_operation(self):
        for operation in analyzers.OPERATIONS:
            result = analyzers.analyze(operation, {'request': 'Look at the order service.',
                                                   'files': [{'path': 'app/service.py', 'content': VULNERABLE}],
                                                   'logs': TRACEBACK_LOG})
            self.assertEqual(set(result), {'schema_version', 'operation', 'status', 'method', 'evidence', 'findings', 'report', 'limitations'})
            self.assertEqual(result['schema_version'], 1)
            self.assertEqual(result['operation'], operation)
            self.assertIn(result['status'], {'completed', 'degraded', 'clarification_required'})
            self.assertEqual(result['method'], {'analysis': 'bounded_static_heuristics', 'code_executed': False, 'repository_fetched': False})
            self.assertTrue(result['limitations'] and all(isinstance(item, str) for item in result['limitations']))
            self.assertIn('inputs', result['report'])

    def test_every_finding_is_anchored_to_returned_evidence(self):
        for operation in analyzers.OPERATIONS:
            result = analyzers.analyze(operation, {'request': 'Review and diagnose the order service.',
                                                   'files': [{'path': 'app/service.py', 'content': VULNERABLE},
                                                             {'path': 'app/api.py', 'content': ROUTED}],
                                                   'logs': TRACEBACK_LOG, 'diff': DIFF})
            identifiers = {item['id'] for item in result['evidence']}
            self.assertTrue(result['findings'], 'expected findings for ' + operation)
            for item in result['findings']:
                self.assertEqual(set(item) >= {'id', 'rule', 'dimension', 'severity', 'kind', 'source', 'path', 'line',
                                               'evidence_id', 'evidence', 'why', 'recommendation'}, True, item)
                self.assertIn(item['severity'], {'high', 'medium', 'low', 'info'})
                self.assertIn(item['kind'], {'observation', 'hypothesis'})
                self.assertTrue(item['path'] and item['line'] > 0, item)
                self.assertIn(item['evidence_id'], identifiers, item)

    def test_evidence_items_reuse_prepared_identifiers(self):
        result = review(files=[{'path': 'app/service.py', 'content': VULNERABLE}])
        prepared = EVIDENCE.prepare({'request': 'x', 'files': [{'path': 'app/service.py', 'content': VULNERABLE}]})
        upstream = {(item['source'], item['path'], item['line']): item['id'] for item in prepared['evidence']}
        shared = [item for item in result['evidence'] if (item['source'], item['path'], item['line']) in upstream]
        self.assertTrue(shared, 'expected citations to land on prepared evidence lines')
        for item in shared:
            self.assertEqual(item['id'], upstream[(item['source'], item['path'], item['line'])])

    def test_unsupported_operation_and_rejected_payload_raise(self):
        for operation in ('', 'deploy', 'CODE_REVIEW', None, 7):
            with self.assertRaises(ValueError):
                analyzers.analyze(operation, {'request': 'hello'})
        for payload in ({}, {'request': ''}, {'request': 'x', 'unknown': 1}, 'not-a-dict'):
            with self.assertRaises(ValueError):
                analyzers.analyze('code_review', payload)


class CodeReview(unittest.TestCase):
    def test_detects_signal_classes_with_severity_and_recommendation(self):
        result = review(files=[{'path': 'app/service.py', 'content': VULNERABLE, 'language': 'python'}])
        found = rules(result)
        for expected in ('sql_string_interpolation', 'command_injection', 'unsafe_deserialization',
                         'n_plus_one_query', 'bare_except', 'mutable_default_argument', 'swallowed_exception'):
            self.assertIn(expected, found)
        for item in result['findings']:
            self.assertTrue(item['recommendation'].strip() and item['why'].strip())
        self.assertEqual(result['report']['verdict'], 'changes_requested')
        self.assertTrue(any(item['severity'] == 'high' for item in result['findings']))

    def test_reports_the_line_the_pattern_matched(self):
        result = review(files=[{'path': 'app/service.py', 'content': VULNERABLE}])
        lines = VULNERABLE.splitlines()
        for item in result['findings']:
            self.assertIn(item['rule'], {spec['rule'] for spec in analyzers.LINE_RULES} | set(analyzers.CONTEXT_RULE_META) | {'duplicated_block'})
            self.assertEqual(' '.join(lines[item['line'] - 1].split()), item['evidence'])

    def test_safe_equivalents_are_not_flagged_and_are_credited(self):
        result = review(files=[{'path': 'app/safe.py', 'content': SAFE}])
        self.assertNotIn('sql_string_interpolation', rules(result))
        self.assertNotIn('n_plus_one_query', rules(result))
        self.assertNotIn('hardcoded_credential', rules(result))
        credited = {item['check'] for item in result['report']['what_looks_good']}
        self.assertTrue({'parameterised_query', 'context_managed_resource'} & credited, credited)
        for item in result['report']['what_looks_good']:
            self.assertIn(item['evidence_id'], {entry['id'] for entry in result['evidence']})

    def test_absence_of_findings_is_never_a_clean_bill_of_health(self):
        result = review(files=[{'path': 'app/trivial.py', 'content': 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n'}])
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['report']['verdict'], 'no_automated_findings')
        blob = json.dumps(result).lower()
        for claim in ('no vulnerabilities', 'no bugs were', 'code is safe', 'fully covered', 'safe to merge', 'approved', 'guaranteed'):
            self.assertNotIn(claim, blob)
        self.assertIsNone(re.search(r'(?<!not that )the code is secure', blob))
        self.assertTrue(any('not that the code is secure' in item for item in result['limitations']))
        self.assertIn('not an approval', result['report']['summary'])

    def test_hardcoded_credential_and_redacted_assignment_are_separated(self):
        result = review(files=[{'path': 'config.py', 'content': 'private_key = "MIIBOgIBAAJBAKj34Gk"\napi_token = "0123456789abcdef"\nauthor = "Jane Doe"\nport = 5432\n'}])
        by_rule = {item['rule']: item for item in result['findings']}
        self.assertTrue({'hardcoded_credential', 'credential_assignment_redacted'} & set(by_rule), by_rule)
        for item in result['findings']:
            self.assertNotIn(item['line'], (3, 4), 'author and port must not be treated as credentials')
        if 'credential_assignment_redacted' in by_rule:
            self.assertEqual(by_rule['credential_assignment_redacted']['kind'], 'hypothesis')
            self.assertIn('redaction marker', by_rule['credential_assignment_redacted']['why'])

    def test_division_check_ignores_paths_inside_strings(self):
        quoted = review(files=[{'path': 'routes.py', 'content': 'PREFIX = "/countries"\nURL = "https://example.test/count"\n'}])
        self.assertNotIn('unchecked_division', rules(quoted))
        divided = review(files=[{'path': 'stats.py', 'content': 'def mean(values):\n    return sum(values) / len(values)\n'}])
        self.assertIn('unchecked_division', rules(divided))

    def test_loop_scope_decides_the_n_plus_one_finding(self):
        inside = 'def f(orders, session):\n    for order in orders:\n        session.query(Customer).filter(Customer.id == order.id).first()\n'
        outside = 'def f(orders, session):\n    rows = session.query(Customer).filter(Customer.id.in_(orders)).all()\n    for order in orders:\n        order.touch()\n'
        self.assertIn('n_plus_one_query', rules(review(files=[{'path': 'a.py', 'content': inside}])))
        self.assertNotIn('n_plus_one_query', rules(review(files=[{'path': 'b.py', 'content': outside}])))

    def test_brace_language_loop_scope(self):
        source = ('async function load(ids, db) {\n  const out = [];\n  for (const id of ids) {\n'
                  '    out.push(await db.query("SELECT 1"));\n  }\n  return out;\n}\n'
                  'async function once(db) {\n  return await db.query("SELECT 1");\n}\n')
        found = rules(review(files=[{'path': 'load.js', 'content': source}]))
        self.assertIn('sequential_await_in_loop', found)
        self.assertEqual([item['line'] for item in review(files=[{'path': 'load.js', 'content': source}])['findings']
                          if item['rule'] == 'sequential_await_in_loop'], [4])

    def test_only_added_diff_lines_are_reported_at_post_change_numbers(self):
        result = review(diff=DIFF)
        self.assertTrue(result['findings'])
        for item in result['findings']:
            self.assertEqual(item['source'], 'diff')
            self.assertEqual(item['path'], 'app/web.py')
            self.assertIn(item['line'], (12, 13, 14), item)
            self.assertIn('Only the diff hunks', item['why'])
        self.assertIn('sql_string_interpolation', rules(result))
        self.assertIn('cross_site_scripting_risk', rules(result))

    def test_missing_source_asks_for_context_instead_of_reviewing_nothing(self):
        result = review(request='Please review my pull request.')
        self.assertEqual(result['status'], 'clarification_required')
        self.assertEqual(result['findings'], [])
        self.assertTrue(result['report']['missing_context'])
        self.assertEqual(result['report']['verdict'], 'clarification_required')

    def test_report_discloses_what_was_and_was_not_checked(self):
        result = review(files=[{'path': 'app/service.py', 'content': VULNERABLE}])
        checked = {entry['dimension']: entry['checks'] for entry in result['report']['dimensions_checked']}
        self.assertTrue({'security', 'performance', 'correctness', 'maintainability'} <= set(checked))
        self.assertIn('sql_string_interpolation', checked['security'])
        self.assertTrue(any('coverage' in item for item in result['report']['checks_not_performed']))
        self.assertTrue(all(item['kind'] == 'hypothesis' for item in result['findings']
                            if item['id'] in {entry['id'] for entry in result['report']['hypotheses']}))


class Debug(unittest.TestCase):
    def test_missing_diagnostics_asks_for_the_error_text(self):
        result = analyzers.analyze('debug', {'request': 'Production is broken, please fix it.'})
        self.assertEqual(result['status'], 'clarification_required')
        self.assertTrue(result['report']['missing_context'])
        self.assertEqual(result['report']['root_cause']['status'], 'not_established')

    def test_generic_error_text_yields_no_root_cause(self):
        result = analyzers.analyze('debug', {'request': 'It fails sometimes.', 'logs': 'ERROR: something went wrong\nERROR: failed\n'})
        self.assertEqual(result['report']['root_cause']['status'], 'not_established')
        self.assertIn('generic', result['report']['root_cause']['why'])
        self.assertEqual(result['report']['proposed_fix']['status'], 'withheld')

    def test_facts_hypotheses_and_location_stay_separate(self):
        result = analyzers.analyze('debug', {'request': 'Expected: a user row\nActual: 500 response\nSteps: GET /api/users\nStarted after the deploy yesterday',
                                             'logs': TRACEBACK_LOG,
                                             'files': [{'path': 'app/service.py', 'content': VULNERABLE}]})
        report = result['report']
        self.assertEqual(report['reproduction']['status'], 'supplied')
        self.assertEqual(report['reproduction']['expected'], 'a user row')
        self.assertTrue(all(item['kind'] == 'observation' for item in report['observed_facts']))
        self.assertTrue(all(item['kind'] == 'hypothesis' and item['discriminating_test'].strip() for item in report['hypotheses']))
        self.assertTrue(any(item['signature'] == 'change_correlation' for item in report['hypotheses']))
        self.assertEqual(report['root_cause']['status'], 'location_identified')
        self.assertEqual(report['root_cause']['failing_location'], 'app/service.py:7')
        self.assertIn('not a cause', report['root_cause']['why'])
        self.assertEqual(report['timeline']['traceback_blocks'], 1)
        self.assertEqual(report['timeline']['http_error_counts'], {'500': 1})
        self.assertEqual(report['stack_frames'][0]['supplied_file'], 'app/service.py')
        self.assertIn('sql_string_interpolation', report['stack_frames'][0]['static_check_at_frame'])

    def test_frame_without_the_file_is_reported_as_unmapped(self):
        result = analyzers.analyze('debug', {'request': 'Crash on startup.', 'logs': TRACEBACK_LOG})
        frame = result['report']['stack_frames'][0]
        self.assertEqual(frame['supplied_file'], '')
        self.assertEqual(frame['source_evidence_id'], '')
        self.assertEqual(result['report']['root_cause']['status'], 'not_established')
        self.assertIn('no stack frame maps to a supplied source line', result['report']['root_cause']['why'])
        self.assertTrue(any('Supply the source' in step for step in result['report']['next_diagnostics']))

    def test_operational_signatures_carry_tests_not_conclusions(self):
        logs = ('2026-10-24T04:00:01Z ERROR connection refused to db:5432\n'
                '2026-10-24T04:00:02Z ERROR context deadline exceeded\n'
                '2026-10-24T04:00:03Z ERROR OOMKilled\n')
        result = analyzers.analyze('debug', {'request': 'Works in staging but not prod.', 'logs': logs})
        signatures = {item['signature'] for item in result['report']['error_signatures']}
        self.assertTrue({'connection_refused', 'timeout', 'out_of_memory'} <= signatures, signatures)
        self.assertTrue(any(item['signature'] == 'environment_difference' for item in result['report']['hypotheses']))
        for item in result['report']['hypotheses']:
            self.assertTrue(item['discriminating_test'].strip())
        self.assertEqual(result['report']['root_cause']['status'], 'not_established')


class TestingStrategy(unittest.TestCase):
    def test_cases_are_grounded_in_the_supplied_source(self):
        result = analyzers.analyze('testing_strategy', {'request': 'How should we test the order API?',
                                                        'files': [{'path': 'app/api.py', 'content': ROUTED},
                                                                  {'path': 'tests/test_api.py', 'content': 'def test_get_order():\n    assert True\n'}]})
        report = result['report']
        identifiers = {item['id'] for item in result['evidence']}
        self.assertEqual(report['scope']['test_files_supplied'], ['tests/test_api.py'])
        self.assertTrue(report['proposed_cases'])
        for case in report['proposed_cases']:
            self.assertIn(case['layer'], {'unit', 'integration', 'end_to_end'})
            self.assertIn(case['evidence_id'], identifiers)
            self.assertEqual(case['path'], 'app/api.py')
            self.assertTrue(case['rationale'].strip())
        targets = ' '.join(case['target'] for case in report['proposed_cases'])
        self.assertIn('get_order', targets)
        self.assertIn('fetch', targets)
        self.assertTrue(any('ValueError' in case['case'] for case in report['proposed_cases']))
        self.assertTrue(any(case['layer'] == 'integration' and '/orders' in case['target'] for case in report['proposed_cases']))

    def test_no_coverage_or_pass_claim_is_made(self):
        result = analyzers.analyze('testing_strategy', {'request': 'Write tests for this.',
                                                        'files': [{'path': 'app/api.py', 'content': ROUTED}]})
        self.assertEqual(result['report']['coverage']['measured'], False)
        blob = json.dumps(result).lower()
        for claim in ('tests pass', 'all tests', '% coverage', 'coverage is', 'we ran', 'test suite passed'):
            self.assertNotIn(claim, blob)
        self.assertFalse(re.search(r'\d+\s*%', blob))

    def test_name_based_gaps_are_labelled_as_heuristics(self):
        result = analyzers.analyze('testing_strategy', {'request': 'Where are we exposed?',
                                                        'files': [{'path': 'app/api.py', 'content': ROUTED},
                                                                  {'path': 'tests/test_api.py', 'content': 'def test_get_order():\n    assert True\n'}]})
        gaps = [item for item in result['findings'] if item['rule'] == 'no_supplied_test_references_unit']
        self.assertTrue(gaps)
        self.assertTrue(all(item['kind'] == 'hypothesis' and 'not a coverage measurement' in item['why'] for item in gaps))
        self.assertNotIn('get_order', [item['title'].split()[-1] for item in gaps])

    def test_requirements_only_plan_invents_no_units(self):
        result = analyzers.analyze('testing_strategy', {'request': 'Test plan for a notification service that must hold 10k rps.'})
        report = result['report']
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(report['proposed_cases'], [])
        self.assertEqual(result['findings'], [])
        self.assertTrue(report['requirements_quoted_from_request'])
        self.assertIn('note', report['scope'])
        self.assertTrue(any('requirement-derived' in item for item in result['limitations']))


class Documentation(unittest.TestCase):
    def test_endpoints_come_only_from_supplied_routing_code(self):
        result = analyzers.analyze('documentation', {'request': 'Write API documentation for the order service.',
                                                     'files': [{'path': 'app/api.py', 'content': ROUTED}]})
        report = result['report']
        self.assertEqual(report['document_type'], 'api_reference')
        self.assertEqual([(item['method'], item['path_pattern']) for item in report['http_routes']], [('GET', '/orders/<order_id>')])
        self.assertEqual(report['http_routes'][0]['line'], 10)
        self.assertEqual([item['name'] for item in report['configuration']], ['ORDER_TIMEOUT', 'UPSTREAM'])
        self.assertTrue(any('ValueError' in item['type'] for item in report['error_reference']))

    def test_absent_routes_are_stated_rather_than_imagined(self):
        result = analyzers.analyze('documentation', {'request': 'Write API documentation for this module.',
                                                     'files': [{'path': 'app/safe.py', 'content': SAFE}]})
        report = result['report']
        self.assertEqual(report['http_routes'], [])
        self.assertIn('Do not document endpoints', report['http_routes_note'])
        self.assertTrue(any('not in the supplied routing code' in item for item in report['never_invent']))

    def test_ungrounded_sections_request_input(self):
        result = analyzers.analyze('documentation', {'request': 'Write a runbook for the payment worker.',
                                                     'files': [{'path': 'app/api.py', 'content': ROUTED}]})
        sections = {item['section']: item for item in result['report']['outline']}
        self.assertEqual(result['report']['document_type'], 'runbook')
        self.assertEqual(sections['Rollback']['status'], 'requires_input')
        self.assertEqual(sections['Escalation']['status'], 'requires_input')
        self.assertTrue(sections['Rollback']['needs'].strip())
        for item in result['report']['outline']:
            self.assertIn(item['status'], {'draft_from_supplied_source', 'requires_input'})

    def test_documentation_gaps_point_at_real_declarations(self):
        result = analyzers.analyze('documentation', {'request': 'Document this module.',
                                                     'files': [{'path': 'app/api.py', 'content': ROUTED}]})
        gaps = result['findings']
        self.assertTrue(gaps)
        identifiers = {item['id'] for item in result['evidence']}
        for item in gaps:
            self.assertEqual(item['rule'], 'undocumented_public_item')
            self.assertEqual(item['path'], 'app/api.py')
            self.assertIn(item['evidence_id'], identifiers)
        self.assertNotIn('get_order', ' '.join(item['title'] for item in gaps))


class Bounds(unittest.TestCase):
    def test_large_payload_stays_inside_every_declared_bound(self):
        body = ''.join('    cursor.execute(f"SELECT {i} FROM t WHERE x = {i}")\n' % () if False else
                       '    cursor.execute(f"SELECT a FROM t WHERE x = {value%d}")\n' % number for number in range(260))
        files = [{'path': 'app/mod%d.py' % index, 'content': 'def handler(cursor, value0):\n' + body} for index in range(4)]
        result = analyzers.analyze('code_review', {'request': 'Review all of it.', 'files': files})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), analyzers.MAX_JSON_BYTES)
        self.assertLessEqual(len(result['findings']), analyzers.MAX_FINDINGS)
        self.assertLessEqual(len(result['evidence']), analyzers.MAX_EVIDENCE)
        self.assertEqual(result['status'], 'degraded')
        self.assertTrue(any('More findings matched' in item for item in result['limitations']))
        identifiers = {item['id'] for item in result['evidence']}
        for item in result['findings']:
            self.assertIn(item['evidence_id'], identifiers)

    def test_line_limit_is_disclosed(self):
        content = ''.join('x=%d\n' % number for number in range(analyzers.MAX_LINES_PER_SOURCE + 200))
        result = analyzers.analyze('code_review', {'request': 'Review.', 'files': [{'path': 'big.py', 'content': content}]})
        self.assertEqual(result['status'], 'degraded')
        self.assertEqual(result['report']['inputs']['line_limit_reached'], ['big.py'])
        self.assertTrue(any('first %d lines' % analyzers.MAX_LINES_PER_SOURCE in item for item in result['limitations']))

    def test_redaction_is_reported_without_degrading_the_analysis(self):
        result = analyzers.analyze('code_review', {'request': 'Review.',
                                                   'files': [{'path': 'c.py', 'content': 'api_token = "0123456789abcdef"\n'}]})
        self.assertEqual(result['status'], 'completed')
        if result['report']['inputs']['redaction_count']:
            self.assertTrue(any('redacted before analysis' in item for item in result['limitations']))


class Boundaries(unittest.TestCase):
    """The analyzers must not reach outside the supplied payload."""

    def test_prepare_is_the_only_input_path(self):
        calls = []
        recorder = types.ModuleType('engineering_evidence')

        def prepare(payload):
            calls.append(payload)
            return {'request': 'x', 'context': '', 'files': [], 'diff': '', 'logs': '',
                    'evidence': [], 'warnings': [], 'redaction_count': 0, 'truncated': False}
        recorder.prepare = prepare
        sys.modules['engineering_evidence'] = recorder
        try:
            payload = {'request': 'Review this.', 'files': [{'path': 'a.py', 'content': 'x = 1\n'}]}
            result = analyzers.analyze('code_review', payload)
            self.assertEqual(calls, [payload])
            self.assertEqual(result['status'], 'clarification_required')
        finally:
            sys.modules['engineering_evidence'] = EVIDENCE

    def test_module_imports_and_calls_stay_inside_the_sandbox(self):
        tree = ast.parse(ANALYZERS.read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        self.assertEqual(imported, {'importlib', 'json', 're', 'sys'})
        forbidden = {'open', 'eval', 'exec', 'compile', '__import__', 'input', 'breakpoint'}
        dynamic = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, forbidden)
                if isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module':
                    dynamic.append(node)
        self.assertEqual(len(dynamic), 1)
        self.assertEqual([getattr(argument, 'id', None) for argument in dynamic[0].args], ['EVIDENCE_MODULE'])

    def test_absent_evidence_module_fails_loudly(self):
        # A sys.meta_path finder returning None does not block PathFinder; other
        # suite modules legitimately add runtime/tools to sys.path. Simulate the
        # unavailable dependency at the actual import boundary instead.
        from unittest.mock import patch
        saved = sys.modules.pop('engineering_evidence')
        try:
            with patch.object(analyzers.importlib, 'import_module', side_effect=ImportError('fixture unavailable')):
                with self.assertRaises(RuntimeError) as caught:
                    analyzers.analyze('code_review', {'request': 'Review.'})
                self.assertEqual(str(caught.exception), 'engineering_evidence_unavailable')
        finally:
            sys.modules['engineering_evidence'] = saved


if __name__ == '__main__':
    print('engineering_evidence under test: ' + EVIDENCE_SOURCE, file=sys.stderr)
    unittest.main(verbosity=2)
