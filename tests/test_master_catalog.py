"""Tests for backend/master_engine/catalog.py (stdlib only, no network/shell)."""
import hashlib
import importlib.util
import json
import pathlib
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    'master_catalog', _REPO / 'backend' / 'master_engine' / 'catalog.py')
mc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mc)

SOURCE = json.loads(
    (_REPO / 'backend' / 'master_engine' / 'catalog_source.json').read_text())
MASTER_TXT = _REPO / 'backend' / 'master_engine' / 'specification.txt'

EXACT_IDS = {'debug.attach', 'debug.breakpoint', 'evidence.chain.custody',
             'evidence.store.secure', 'test.coverage', 'test.run', 'test.single'}
RESTRICTED_BY_CATEGORY = {'exploit_execution': 15, 'credential_attacks': 11,
                          'post_exploitation': 6, 'binary_injection': 10,
                          'stealth_anti_bot_bypass': 5}


def _noop(payload):
    return {'result': payload}


class SourceIntegrityTest(unittest.TestCase):
    def test_sha256_matches_frozen_value(self):
        digest = hashlib.sha256(MASTER_TXT.read_bytes()).hexdigest()
        self.assertEqual(digest, SOURCE['source']['sha256'])

    def test_az_counts_1203_1200_and_apply_diff4(self):
        az = SOURCE['az']
        self.assertEqual(az['total_entries'], 1203)
        self.assertEqual(az['unique_ids'], 1200)
        self.assertEqual(az['duplicate_occurrences'], 3)
        self.assertEqual(len(az['primary_ids']), 1200)
        self.assertEqual(az['duplicates'],
                         {'rebuild.apply_diff': {'count': 4,
                                                 'lines': [1339, 1376, 1393, 1404]}})

    def test_no_invented_ids(self):
        text = MASTER_TXT.read_text()
        for skill_id in SOURCE['az']['primary_ids']:
            self.assertIn(skill_id, text)


class MetadataMathTest(unittest.TestCase):
    def test_breakdown_sums_1408_declared_1507_gap_99(self):
        detailed = [e for e in SOURCE['engines'] if isinstance(e['skills'], int)]
        self.assertEqual(len(SOURCE['engines']), 12)
        self.assertEqual(len(detailed), 11)
        self.assertEqual(sum(e['skills'] for e in detailed), 1408)
        self.assertEqual(SOURCE['declared']['total_skills'], 1507)
        self.assertEqual(1507 - 1408, 99)

    def test_documents_engine_is_narrative_only(self):
        docs = [e for e in SOURCE['engines'] if not isinstance(e['skills'], int)]
        self.assertEqual([e['name'] for e in docs],
                         ['Documents Skill (Refactoring)'])

    def test_category_and_phase_sums(self):
        cats = [c for c in SOURCE['categories'] if isinstance(c['skills'], int)]
        self.assertEqual(sum(c['skills'] for c in cats), 1408)
        # Source table 1.3 lists 13 engine rows for 12 declared engines: the
        # Documents engine is counted both inside Content (2) and standalone.
        self.assertEqual(sum(c['engines'] for c in SOURCE['categories']), 13)
        self.assertEqual(SOURCE['declared']['total_engines'], 12)
        self.assertEqual(sum(p['skills'] for p in SOURCE['phases']), 1507)

    def test_registry_index_has_601(self):
        self.assertEqual(len(SOURCE['registry_index']), 601)


class ReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.stats = mc.stats()
        self.skills = mc.catalog()['skills']

    def test_statuses_exclusive_and_sum_1200(self):
        counts = {}
        for entry in self.skills:
            counts[entry['status']] = counts.get(entry['status'], 0) + 1
        self.assertEqual(sum(counts.values()), 1200)
        self.assertEqual(counts, self.stats['status_counts'])
        self.assertEqual(counts[mc.STATUS_EXACT], 7)
        self.assertEqual(counts[mc.STATUS_RESTRICTED], 47)
        self.assertEqual(counts[mc.STATUS_CONNECTOR], 14)
        self.assertEqual(counts[mc.STATUS_MISSING], 1132)

    def test_exact_registered_ids(self):
        self.assertEqual(set(self.stats['exact_registered_ids']), EXACT_IDS)
        for entry in self.skills:
            if entry['status'] == mc.STATUS_EXACT:
                self.assertEqual(entry['domain_alignment'], 'cross_domain')
                self.assertFalse(entry['registered_implies_operational'])
                self.assertFalse(entry['operational'])

    def test_restricted_policy_counts(self):
        self.assertEqual(self.stats['restricted_by_category'], RESTRICTED_BY_CATEGORY)
        restricted = {e['id'] for e in self.skills
                      if e['status'] == mc.STATUS_RESTRICTED}
        self.assertIn('exploit.reverse_shell', restricted)
        self.assertIn('account.credential_stuff', restricted)
        self.assertIn('post.creds.dump', restricted)
        self.assertIn('inject.dll_proxy', restricted)
        self.assertIn('smart.anti_bot_bypass', restricted)
        for entry in self.skills:
            if entry['status'] == mc.STATUS_RESTRICTED:
                self.assertEqual(entry['handler_binding'], 'forbidden')

    def test_connector_namespace_unconfigured(self):
        connectors = [e for e in self.skills
                      if e['status'] == mc.STATUS_CONNECTOR]
        self.assertEqual(len(connectors), 14)
        for entry in connectors:
            self.assertTrue(entry['id'].startswith('connector.'))
            self.assertEqual(entry['connector_namespace'], 'connector.')

    def test_related_ids_are_not_aliases(self):
        by_id = {e['id']: e for e in self.skills}
        kb = by_id['kb.faq.search']
        self.assertEqual(kb['status'], mc.STATUS_MISSING)
        self.assertTrue(kb['related_registry_ids'])
        self.assertTrue(kb['related_ids_are_not_aliases'])
        self.assertEqual(kb['related_by'], 'family_token')

    def test_registered_never_counted_operational(self):
        self.assertEqual(self.stats['operational_skills'], 0)
        self.assertFalse(self.stats['registered_implies_operational'])


class DomainCollisionTest(unittest.TestCase):
    def test_evidence_chain_custody_is_legal_not_security_forensics(self):
        result = mc.lookup('evidence.chain.custody')
        entry = result['catalog_entry']
        self.assertEqual(entry['status'], mc.STATUS_EXACT)
        self.assertEqual(entry['registry_worker'], 'legal')
        self.assertEqual(entry['domain_alignment'], 'cross_domain')
        self.assertIn('legal', entry['ambiguity_note'])
        self.assertIn('case_id', result['descriptor']['input_schema']['required'])
        self.assertFalse(result['registered_implies_operational'])

    def test_coding_exact_matches_marked_cross_domain(self):
        for skill_id in ('test.run', 'debug.attach'):
            entry = mc.lookup(skill_id)['catalog_entry']
            self.assertEqual(entry['registry_worker'], 'coding')
            self.assertIsNotNone(entry['ambiguity_note'])


class LookupTest(unittest.TestCase):
    def test_lookup_returns_deep_copy(self):
        first = mc.lookup('test.run')
        first['descriptor']['id'] = 'tampered'
        first['catalog_entry']['status'] = 'tampered'
        second = mc.lookup('test.run')
        self.assertEqual(second['descriptor']['id'], 'test.run')
        self.assertEqual(second['catalog_entry']['status'], mc.STATUS_EXACT)

    def test_unimplemented_raises_skill_unavailable(self):
        cases = {'browser.launch': mc.STATUS_MISSING,
                 'clone.deploy.vercel': mc.STATUS_MISSING,
                 'connector.shopify': mc.STATUS_CONNECTOR,
                 'exploit.msfrpc': mc.STATUS_RESTRICTED,
                 'rebuild.apply_diff': mc.STATUS_MISSING}
        for skill_id, status in cases.items():
            with self.assertRaises(mc.SkillUnavailable) as ctx:
                mc.lookup(skill_id)
            self.assertEqual(ctx.exception.skill_id, skill_id)
            self.assertEqual(ctx.exception.status, status)

    def test_unknown_id_raises(self):
        with self.assertRaises(mc.SkillUnavailable):
            mc.lookup('totally.fake.skill')

    def test_duplicate_id_reports_occurrences(self):
        with self.assertRaises(mc.SkillUnavailable) as ctx:
            mc.lookup('rebuild.apply_diff')
        by_id = {e['id']: e for e in mc.catalog()['skills']}
        entry = by_id['rebuild.apply_diff']
        self.assertEqual(entry['occurrences'], 4)
        self.assertEqual(entry['duplicate_source_lines'],
                         [1339, 1376, 1393, 1404])
        self.assertEqual(ctx.exception.status, mc.STATUS_MISSING)


class HandlerInjectionTest(unittest.TestCase):
    def test_exact_registered_accepts_handler(self):
        stats = mc.configure(handlers={'test.run': _noop})
        try:
            self.assertEqual(stats['operational_skills'], 1)
            result = mc.lookup('test.run')
            self.assertTrue(result['handler_installed'])
            self.assertTrue(result['operational'])
        finally:
            mc.configure()

    def test_restricted_missing_connector_handlers_rejected(self):
        for skill_id in ('exploit.search', 'browser.launch', 'connector.xendit',
                         'not.in.catalog'):
            with self.assertRaises(ValueError):
                mc.configure(handlers={skill_id: _noop})
        with self.assertRaises(ValueError):
            mc.configure(handlers={'test.run': 'not-callable'})
        mc.configure()

    def test_snapshot_mutation_is_safe(self):
        first = mc.catalog()
        first['skills'][0]['id'] = 'tampered'
        first['counts']['primary_ids'] = -1
        second = mc.catalog()
        self.assertEqual(len(second['skills']), 1200)
        self.assertEqual(second['skills'][0]['id'], 'account.brute_force')
        self.assertEqual(second['counts']['primary_ids'], 1200)

    def test_stats_mutation_is_safe(self):
        stats = mc.stats()
        stats['primary_ids'] = -1
        stats['status_counts'][mc.STATUS_MISSING] = -1
        self.assertEqual(mc.stats()['primary_ids'], 1200)
        self.assertEqual(mc.stats()['status_counts'][mc.STATUS_MISSING], 1132)

    def test_injected_descriptors_and_drift(self):
        snapshot = SOURCE['exact_match_snapshots']
        stats = mc.configure(existing_descriptors=dict(snapshot))
        try:
            self.assertEqual(stats['registry_descriptors'], 7)
            self.assertEqual(stats['registry_drift']['frozen_registry_count'], 601)
            self.assertEqual(stats['registry_drift']['exact_ids_present'],
                             sorted(EXACT_IDS))
        finally:
            mc.configure()

    def test_malformed_injected_descriptor_rejected(self):
        bad = dict(SOURCE['exact_match_snapshots'])
        bad['test.run'] = {'id': 'test.run'}
        with self.assertRaises(ValueError):
            mc.configure(existing_descriptors=bad)
        with self.assertRaises(ValueError):
            mc.configure(existing_descriptors={'browser.launch': {'id': 'other'}})
        mc.configure()


class IsolationTest(unittest.TestCase):
    def test_module_imports_no_network_shell_or_exec(self):
        import ast
        tree = ast.parse(
            (_REPO / 'backend' / 'master_engine' / 'catalog.py').read_text())
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split('.')[0])
        self.assertEqual(roots, {'copy', 'json', 'pathlib'})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ('eval', 'exec', 'compile', 'open'))
                self.assertNotEqual(node.func.id, '__import__')

    def test_entries_are_copy(self):
        entries = mc._DEFAULT.entries()
        entries[0]['status'] = 'tampered'
        self.assertEqual(mc._DEFAULT.entries()[0]['status'],
                         mc.STATUS_RESTRICTED)


if __name__ == '__main__':
    unittest.main()
