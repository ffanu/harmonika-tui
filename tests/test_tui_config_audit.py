"""Tests for the Harmonika TUI config auditor (runtime/tools/tui_config_audit.py).

The auditor is loaded directly from the file so the tests do not depend on
package installation. Coverage: mechanics-only contract, legacy vs native V2
field distinction, unpinned third-party plugins, untrusted public API hosts,
auto-update flags, value redaction, read-only behaviour and input guards.
"""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'tui_config_audit', ROOT / 'runtime/tools/tui_config_audit.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def codes(result):
    return [finding['code'] for finding in result['findings']]


class ContractTests(unittest.TestCase):
    def test_empty_config_is_clean_and_reports_unknown_generation(self):
        result = audit.audit_config({})
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['engine_generation'], 'unknown')
        self.assertIs(result['audit_only'], True)
        self.assertIs(result['config_preserved'], True)
        self.assertIs(result['config_modified'], False)
        self.assertTrue(result['limitations'])

    def test_result_schema_is_stable(self):
        result = audit.audit_config({'permissions': {}})
        self.assertEqual(result['audit'], 'tui_config_audit')
        self.assertEqual(result['schema'], 1)
        self.assertEqual(
            sorted(result),
            ['audit', 'audit_only', 'config_modified', 'config_preserved',
             'engine_generation', 'finding_codes', 'findings', 'limitations',
             'schema', 'summary'])
        finding = result['findings'][0]
        self.assertEqual(sorted(finding), ['code', 'path', 'severity'])

    def test_brand_version_is_never_engine_proof(self):
        result = audit.audit_config({
            'brand': 'Harmonika custom 1.0.0', 'version': '1.0.0',
            'product': 'Harmonika Terminal'})
        self.assertEqual(result['engine_generation'], 'unknown')
        self.assertEqual(result['findings'], [])
        self.assertNotIn('v1', json.dumps(result))
        self.assertNotIn('1.0.0', json.dumps(result))


class GenerationTests(unittest.TestCase):
    def test_legacy_permission_bash_task_mechanics(self):
        result = audit.audit_config({
            'tools': {'permission': 'ask', 'bash': {'allowed': []},
                      'task': 'manual'}})
        self.assertEqual(codes(result), ['legacy_bash_field',
                                         'legacy_permission_field',
                                         'legacy_task_field'])
        paths = [finding['path'] for finding in result['findings']]
        self.assertEqual(paths, ['tools.bash', 'tools.permission', 'tools.task'])
        self.assertEqual(result['engine_generation'], 'legacy')

    def test_native_v2_permissions_shell_subagent_mechanics(self):
        result = audit.audit_config({
            'tools': {'permissions': {}, 'shell': 'guarded',
                      'subagent': {'enabled': True}}})
        self.assertEqual(codes(result), ['native_permissions_field',
                                         'native_shell_field',
                                         'native_subagent_field'])
        self.assertEqual(result['engine_generation'], 'native_v2')

    def test_mixed_mechanics_reported_separately(self):
        result = audit.audit_config({'bash': {}, 'shell': {}})
        self.assertEqual(result['engine_generation'], 'mixed')
        self.assertEqual(codes(result), ['legacy_bash_field',
                                         'native_shell_field'])


class PluginTests(unittest.TestCase):
    def test_unpinned_npm_version(self):
        result = audit.audit_config(
            {'plugins': [{'source': {'kind': 'npm', 'name': 'x',
                                     'version': '^1.0.0'}}]})
        self.assertEqual(codes(result), ['plugin_version_unpinned'])
        self.assertEqual(result['findings'][0]['path'], 'plugins[0]')
        self.assertEqual(result['findings'][0]['severity'], 'high')

    def test_missing_npm_version(self):
        result = audit.audit_config(
            {'plugins': [{'kind': 'npm'}]})
        self.assertEqual(codes(result), ['plugin_version_unpinned'])

    def test_latest_and_range_versions_are_unpinned(self):
        for version in ('latest', '*', '>=2.0.0', '1 - 2', '1.0.0 || 2.0.0'):
            result = audit.audit_config(
                {'plugins': [{'source': {'kind': 'npm', 'version': version}}]})
            self.assertEqual(codes(result), ['plugin_version_unpinned'], version)

    def test_pinned_npm_without_integrity(self):
        result = audit.audit_config(
            {'plugins': [{'source': {'kind': 'npm', 'version': '1.2.3'}}]})
        self.assertEqual(codes(result), ['plugin_integrity_missing'])
        self.assertEqual(result['findings'][0]['severity'], 'medium')

    def test_fully_pinned_npm_is_clean(self):
        result = audit.audit_config({'plugins': [
            {'source': {'kind': 'npm', 'version': '1.2.3',
                        'integrity': 'sha512-abc'}}]})
        self.assertEqual(result['findings'], [])

    def test_git_without_ref_is_unpinned(self):
        result = audit.audit_config(
            {'plugins': [{'source': {'kind': 'git'}}]})
        self.assertEqual(codes(result), ['plugin_ref_unpinned'])

    def test_git_mutable_branch_ref(self):
        result = audit.audit_config(
            {'plugins': [{'source': {'kind': 'github', 'ref': 'main'}}]})
        self.assertEqual(codes(result), ['plugin_ref_mutable'])

    def test_git_pinned_commit_is_clean(self):
        result = audit.audit_config({'plugins': [
            {'source': {'kind': 'git',
                        'commit': '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b'}}]})
        self.assertEqual(result['findings'], [])

    def test_string_plugin_entries(self):
        result = audit.audit_config({'plugins': ['npm:demo-plugin@^2.1.0',
                                                 'git+https://example.invalid/p.git']})
        self.assertEqual(codes(result), ['plugin_version_unpinned',
                                         'plugin_ref_unpinned'])

    def test_local_plugins_are_not_third_party(self):
        result = audit.audit_config({'plugins': [
            {'kind': 'local'}, {'source': {'kind': 'builtin'}}, 'local:demo']})
        self.assertEqual(result['findings'], [])


class HostTests(unittest.TestCase):
    def test_untrusted_remote_public_api_host(self):
        result = audit.audit_config(
            {'api_base_url': 'https://api.openai.com/v1'})
        self.assertEqual(codes(result), ['untrusted_api_host'])
        self.assertEqual(result['findings'][0]['path'], 'api_base_url')
        self.assertEqual(result['findings'][0]['trusted_hosts'],
                         ['chat.harmonika.id'])
        self.assertNotIn('openai', json.dumps(result))

    def test_trusted_public_api_host_is_clean(self):
        result = audit.audit_config(
            {'api_base_url': 'https://chat.harmonika.id/v1',
             'public_api_host': 'chat.harmonika.id'})
        self.assertEqual(result['findings'], [])

    def test_doc_host_allowed_only_for_doc_context(self):
        clean = audit.audit_config(
            {'docs': {'url': 'https://hai.harmonika.id/guide'}})
        self.assertEqual(clean['findings'], [])
        mismatch = audit.audit_config(
            {'api': {'base_url': 'https://hai.harmonika.id/v1'}})
        self.assertEqual(codes(mismatch), ['untrusted_api_host'])

    def test_local_hosts_are_not_remote_mismatches(self):
        for value in ('http://localhost:8080', 'http://127.0.0.1:9000',
                      'http://192.168.1.4/api', 'http://engine.local',
                      'https://[::1]/v1'):
            result = audit.audit_config({'api_base_url': value})
            self.assertEqual(result['findings'], [], value)

    def test_generic_url_key_requires_api_context(self):
        clean = audit.audit_config(
            {'plugins': [{'source': {'kind': 'git',
                                     'url': 'https://example.invalid/p.git',
                                     'ref': 'v1.0.0'}}]})
        self.assertEqual(clean['findings'], [])
        flagged = audit.audit_config(
            {'provider': {'url': 'https://example.invalid/v1'}})
        self.assertEqual(codes(flagged), ['untrusted_api_host'])

    def test_host_value_never_echoed(self):
        result = audit.audit_config({'api_url': 'https://api.evil.example/v1'})
        self.assertEqual(codes(result), ['untrusted_api_host'])
        self.assertNotIn('evil.example', json.dumps(result))


class AutoUpdateTests(unittest.TestCase):
    def test_global_auto_update_flag(self):
        result = audit.audit_config({'auto_update': True})
        self.assertEqual(codes(result), ['auto_update_enabled'])
        self.assertEqual(result['findings'][0]['severity'], 'high')

    def test_nested_update_automatic_flag(self):
        result = audit.audit_config({'update': {'automatic': True}})
        self.assertEqual(codes(result), ['auto_update_enabled'])
        self.assertEqual(result['findings'][0]['path'], 'update.automatic')

    def test_disabled_auto_update_and_unrelated_auto_keys(self):
        result = audit.audit_config({
            'auto_update': False,
            'update': {'auto': False},
            'formatting': {'auto': True, 'automatic': True}})
        self.assertEqual(result['findings'], [])


class RedactionTests(unittest.TestCase):
    def test_values_are_never_echoed(self):
        config = {
            'provider_api_key': 'sk-VERYSECRETKEY',
            'password': 'hunter2-secret',
            'model_response': 'RESPONSE-PAYLOAD',
            'user_email': 'person@example.invalid',
            'plugins': [{'source': {'kind': 'npm', 'name': 'secret-plugin-name',
                                    'version': '^1.0.0'}}],
            'api_base_url': 'https://api.secret-host.example/v1',
        }
        result = audit.audit_config(config)
        dumped = json.dumps(result)
        for secret in ('sk-VERYSECRETKEY', 'hunter2-secret',
                       'RESPONSE-PAYLOAD', 'person@example.invalid',
                       'secret-plugin-name', 'api.secret-host.example'):
            self.assertNotIn(secret, dumped)
        self.assertTrue(result['findings'])

    def test_findings_only_carry_codes_and_paths(self):
        config = {'auto_update': True, 'bash': {}, 'plugins': [{'kind': 'npm'}],
                  'api_url': 'https://example.invalid'}
        result = audit.audit_config(config)
        for finding in result['findings']:
            self.assertEqual(sorted(set(finding) - {'trusted_hosts'}),
                             ['code', 'path', 'severity'])

    def test_summary_counts_match(self):
        result = audit.audit_config(
            {'auto_update': True, 'bash': {}, 'permissions': {},
             'plugins': [{'kind': 'npm'}]})
        summary = result['summary']
        self.assertEqual(summary['total'], len(result['findings']))
        self.assertEqual(summary['high'], 2)
        self.assertEqual(summary['medium'], 1)
        self.assertEqual(summary['info'], 1)
        self.assertEqual(summary['critical'] + summary['low'], 0)


class SafetyTests(unittest.TestCase):
    def test_config_is_never_mutated(self):
        config = {'tools': {'bash': {'allowed': True}},
                  'plugins': [{'source': {'kind': 'npm', 'version': '^1'}}],
                  'update': {'automatic': True}}
        before = copy.deepcopy(config)
        audit.audit_config(config)
        self.assertEqual(config, before)

    def test_invalid_inputs_are_rejected_with_stable_codes(self):
        for value, code in (([], 'invalid_config'), ('x', 'invalid_config'),
                            (None, 'invalid_config'),
                            ({'k': float('nan')}, 'invalid_number')):
            with self.assertRaises(audit.ConfigAuditError) as ctx:
                audit.audit_config(value)
            self.assertEqual(ctx.exception.code, code)

    def test_depth_and_size_limits(self):
        nested = {'value': 0}
        for _ in range(20):
            nested = {'value': nested}
        with self.assertRaises(audit.ConfigAuditError) as ctx:
            audit.audit_config(nested)
        self.assertEqual(ctx.exception.code, 'input_complexity_limit')
        with self.assertRaises(audit.ConfigAuditError) as ctx:
            audit.audit_config({'blob': 'x' * 600000})
        self.assertEqual(ctx.exception.code, 'input_byte_limit')

    def test_non_string_keys_rejected(self):
        with self.assertRaises(audit.ConfigAuditError) as ctx:
            audit.audit_config({1: 'x'})
        self.assertEqual(ctx.exception.code, 'invalid_json_key')

    def test_findings_are_deterministic(self):
        config = {'bash': {}, 'auto_update': True, 'permissions': {}}
        first = audit.audit_config(config)
        second = audit.audit_config(config)
        self.assertEqual(first, second)


class NativeCompatibilityPrivacy(unittest.TestCase):
    def test_camel_case_endpoint_and_dynamic_account_field_names_are_not_exposed(self):
        value={'providers':{'PRIVATE_ACCOUNT_SENTINEL':{'options':{'baseURL':'https://untrusted.example.invalid/v1'}}}}
        result=audit.audit_config(value)
        self.assertIn('untrusted_api_host',codes(result))
        self.assertNotIn('PRIVATE_ACCOUNT_SENTINEL',json.dumps(result))
        self.assertNotIn('untrusted.example.invalid',json.dumps(result))


if __name__ == '__main__':
    unittest.main()
