"""Tests for the Harmonika trust & safety engine (runtime/tools/trust_engine.py).

The engine is loaded directly from the file so the tests do not depend on package
installation. Coverage: contract surface, refusal boundaries, redaction, every
operation, and the input-validation guard rails.
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('trust_engine', ROOT / 'runtime/tools/trust_engine.py')
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


def login_events(count, outcome='fail', account='alice', ip='203.0.113.9'):
    return [{'id': 'e%d' % index, 'ts': '2026-11-02T10:%02d:00Z' % index, 'kind': 'login',
             'outcome': outcome, 'account': account, 'ip': ip, 'device_id': 'd1',
             'country': 'ID'} for index in range(count)]


class ContractTests(unittest.TestCase):
    def test_operation_set_is_exact(self):
        self.assertEqual(sorted(engine.OPERATIONS), sorted({
            'trust.capabilities', 'trust.fraud_risk', 'trust.captcha_audit',
            'trust.privacy_audit', 'trust.bot_defense_plan'}))

    def test_capabilities_flag_offensive_abilities_as_absent(self):
        caps = engine.capabilities()
        for key in ('fingerprint_evasion_tooling', 'captcha_solving', 'detection_circumvention',
                    'network_access', 'source_execution', 'pii_retention'):
            self.assertIs(caps['features'][key], False, key)
        for key in ('defensive_rule_generation', 'privacy_mitigations', 'integration_plan'):
            self.assertIs(caps['features'][key], True, key)
        self.assertEqual([row['id'] for row in caps['operations']], sorted(engine.OPERATIONS))

    def test_results_never_claim_confirmation_or_retention(self):
        result = engine.call('trust.fraud_risk', {'events': login_events(2)})
        self.assertIs(result['attack_confirmed'], False)
        self.assertIs(result['enforcement_performed'], False)
        self.assertIs(result['telemetry_retained'], False)
        self.assertTrue(result['limitations'])

    def test_unknown_and_invalid_inputs_are_rejected(self):
        with self.assertRaises(engine.TrustError) as ctx:
            engine.call('trust.fingerprint_spoof', {})
        self.assertEqual(ctx.exception.code, 'unknown_operation')
        with self.assertRaises(engine.TrustError) as ctx:
            engine.call('trust.fraud_risk', {'events': login_events(1), 'unknown_key': 1})
        self.assertEqual(ctx.exception.code, 'invalid_fields')
        with self.assertRaises(engine.TrustError) as ctx:
            engine.call('trust.fraud_risk', {'events': []})
        self.assertEqual(ctx.exception.code, 'no_events')
        with self.assertRaises(engine.TrustError) as ctx:
            engine.call('trust.captcha_audit', {'provider': 'solve-my-captcha'})
        self.assertEqual(ctx.exception.code, 'invalid_enum')


class FraudTests(unittest.TestCase):
    def test_brute_force_is_flagged_with_pseudonymous_subject(self):
        result = engine.call('trust.fraud_risk', {
            'events': login_events(6), 'thresholds': {'failed_logins_per_account': 5}})
        rules = [row['rule_id'] for row in result['findings']]
        self.assertIn('FRAUD.LOGIN.VELOCITY', rules)
        for row in result['findings']:
            self.assertTrue(row['subject'].startswith(('account:ref_', 'ip:ref_', 'session:ref_',
                                                       'telemetry:')), row['subject'])
            self.assertNotIn('alice', json.dumps(row))
            self.assertNotIn('203.0.113.9', json.dumps(row))

    def test_credential_stuffing_across_accounts(self):
        events = []
        for index in range(6):
            events.append({'id': 'c%d' % index, 'ts': '2026-11-02T10:0%d:00Z' % index,
                           'kind': 'login', 'outcome': 'fail', 'account': 'user%d' % index,
                           'ip': '198.51.100.7'})
        result = engine.call('trust.fraud_risk', {'events': events})
        self.assertIn('FRAUD.CREDENTIAL_STUFFING', [row['rule_id'] for row in result['findings']])

    def test_known_device_drift_and_impossible_travel(self):
        events = [
            {'id': 'a1', 'ts': '2026-11-02T10:00:00Z', 'kind': 'login', 'outcome': 'success',
             'account': 'bob', 'ip': '198.51.100.1', 'device_id': 'new-device', 'country': 'ID'},
            {'id': 'a2', 'ts': '2026-11-02T10:20:00Z', 'kind': 'login', 'outcome': 'success',
             'account': 'bob', 'ip': '198.51.100.1', 'device_id': 'new-device', 'country': 'DE'},
        ]
        result = engine.call('trust.fraud_risk', {'events': events, 'window_minutes': 120,
                                                  'known_devices': {'bob': ['known-1']}})
        rules = {row['rule_id'] for row in result['findings']}
        self.assertIn('FRAUD.DEVICE.BINDING_DRIFT', rules)
        self.assertIn('FRAUD.GEO.IMPOSSIBLE_TRAVEL', rules)

    def test_client_hint_mismatch_is_detected(self):
        events = [{'id': 'h1', 'ts': '2026-11-02T10:00:00Z', 'kind': 'login', 'outcome': 'success',
                   'account': 'carol', 'ip': '198.51.100.2',
                   'ua': 'Mozilla/5.0 (Windows NT 10.0) Chrome/120',
                   'client_hints': {'sec_ch_ua': True, 'sec_ch_ua_platform': 'macOS'}}]
        result = engine.call('trust.fraud_risk', {'events': events})
        self.assertIn('FRAUD.CLIENT_HINTS.MISMATCH', [row['rule_id'] for row in result['findings']])

    def test_clean_traffic_has_no_findings_and_allow_decision(self):
        result = engine.call('trust.fraud_risk', {'events': login_events(1, outcome='success')})
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['subjects'], [])
        self.assertIs(result['attack_confirmed'], False)

    def test_decision_escalates_with_accumulated_score(self):
        events = []
        for index in range(6):
            events.append({'id': 'p%d' % index, 'ts': '2026-11-02T10:0%d:00Z' % index,
                           'kind': 'payment', 'outcome': 'fail', 'account': 'dave',
                           'ip': '198.51.100.3', 'card_bin': '4%05d' % index})
        result = engine.call('trust.fraud_risk', {'events': events})
        self.assertGreaterEqual(max(row['risk_score'] for row in result['subjects']), 40)
        self.assertIn(result['subjects'][0]['recommended_decision'], ('challenge', 'block'))


class CaptchaTests(unittest.TestCase):
    HARDENED = {'provider': 'turnstile',
                'client': {'site_key_present': True, 'widget_on_auth': True},
                'server': {'siteverify_called': True, 'secret_from_env': True,
                           'token_single_use': True, 'expiry_seconds': 300,
                           'hostname_checked': True, 'remoteip_checked': True},
                'rate_limit': {'per_ip_per_minute': 10, 'per_account_per_hour': 20},
                'other': {'honeypot': True, 'csrf_token': True,
                          'content_security_policy': True, 'logs_bot_signals': True}}

    def test_hardened_deployment_has_no_findings(self):
        result = engine.call('trust.captcha_audit', self.HARDENED)
        self.assertEqual(result['findings'], [])

    def test_missing_server_verification_is_critical(self):
        payload = dict(self.HARDENED)
        payload['server'] = {'siteverify_called': False, 'secret_from_env': True}
        result = engine.call('trust.captcha_audit', payload)
        rows = {row['rule_id']: row for row in result['findings']}
        self.assertIn('CAPTCHA.SERVER.VERIFICATION_MISSING', rows)
        self.assertEqual(rows['CAPTCHA.SERVER.VERIFICATION_MISSING']['severity'], 'critical')

    def test_recaptcha_v3_requires_action_and_score(self):
        payload = {'provider': 'recaptcha_v3', 'client': {'site_key_present': True},
                   'server': {'siteverify_called': True, 'secret_from_env': True,
                              'token_single_use': True, 'expiry_seconds': 120,
                              'hostname_checked': True, 'action_checked': False,
                              'score_threshold': 0.2}}
        rules = {row['rule_id'] for row in engine.call('trust.captcha_audit', payload)['findings']}
        self.assertIn('CAPTCHA.SERVER.ACTION_UNCHECKED', rules)
        self.assertIn('CAPTCHA.SERVER.SCORE_LOW', rules)

    def test_no_provider_recommends_deployment(self):
        result = engine.call('trust.captcha_audit', {'provider': 'none'})
        self.assertIn('CAPTCHA.DEPLOYMENT.MISSING', [row['rule_id'] for row in result['findings']])
        self.assertIs(result['live_verification_performed'], False)
        self.assertTrue(result['recommended_flow'])


class PrivacyTests(unittest.TestCase):
    def test_fingerprint_surface_detection(self):
        source = ("const r = gl.getParameter(gl.getExtension('WEBGL_debug_renderer_info')"
                  ".UNMASKED_RENDERER_WEBGL);\nnew RTCPeerConnection();\n"
                  "localStorage.setItem('auth_token', token);\n")
        result = engine.call('trust.privacy_audit', {
            'files': [{'path': 'static/app.js', 'content': source}]})
        rules = {row['rule_id'] for row in result['findings']}
        self.assertIn('PRIVACY.WEBGL.RENDERER', rules)
        self.assertIn('PRIVACY.RTCP.LOCAL_ADDRESS', rules)
        self.assertIn('PRIVACY.STORAGE.TOKEN', rules)
        for row in result['findings']:
            self.assertNotIn('app.js', row['subject'])
            self.assertTrue(row['subject'].startswith('file:ref_'))

    def test_cookie_flags_are_enforced(self):
        result = engine.call('trust.privacy_audit', {'cookies': [
            {'name': 'session', 'secure': False, 'httponly': False,
             'samesite': 'none', 'max_age_days': 400}]})
        rules = {row['rule_id'] for row in result['findings']}
        for expected in ('PRIVACY.COOKIE.MISSING_SECURE', 'PRIVACY.COOKIE.MISSING_HTTPONLY',
                         'PRIVACY.COOKIE.SAMESITE_NONE_INSECURE',
                         'PRIVACY.COOKIE.LONG_LIFETIME'):
            self.assertIn(expected, rules)

    def test_hardened_headers_pass(self):
        headers = {'content-security-policy': "default-src 'self'",
                   'strict-transport-security': 'max-age=31536000',
                   'referrer-policy': 'strict-origin-when-cross-origin',
                   'permissions-policy': 'geolocation=()',
                   'cross-origin-opener-policy': 'same-origin',
                   'x-content-type-options': 'nosniff',
                   'cache-control': 'no-store'}
        result = engine.call('trust.privacy_audit', {'headers': headers})
        self.assertEqual(result['findings'], [])

    def test_missing_csp_and_unsafe_inline(self):
        result = engine.call('trust.privacy_audit', {'headers': {
            'content-security-policy': "default-src 'self' 'unsafe-inline'"}})
        rules = {row['rule_id'] for row in result['findings']}
        self.assertIn('SECURITY.CSP.UNSAFE_INLINE', rules)
        self.assertIn('SECURITY.HEADER.HSTS_MISSING', rules)


class PlanTests(unittest.TestCase):
    def test_plan_is_layered_and_excludes_evasion(self):
        result = engine.call('trust.bot_defense_plan', {'surface': 'auth', 'stack': 'nextjs'})
        stages = [row['stage'] for row in result['stages']]
        self.assertEqual(stages, ['edge', 'identity', 'session', 'payments',
                                  'observability', 'incident'])
        self.assertTrue(result['excluded_shortcuts'])
        joined = ' '.join(result['excluded_shortcuts']).lower()
        self.assertIn('captcha', joined)
        self.assertIn('sidik jari', joined)
        self.assertIs(result['implementation_performed'], False)

    def test_unknown_surface_rejected(self):
        with self.assertRaises(engine.TrustError) as ctx:
            engine.call('trust.bot_defense_plan', {'surface': 'fraud-radar-bypass'})
        self.assertEqual(ctx.exception.code, 'invalid_enum')


class BoundTests(unittest.TestCase):
    def test_oversized_event_list_is_rejected(self):
        with self.assertRaises(engine.TrustError) as ctx:
            engine.call('trust.fraud_risk', {'events': login_events(engine.MAX_EVENTS + 1)})
        self.assertIn(ctx.exception.code, ('too_many_events', 'input_byte_limit'))

    def test_non_json_values_rejected(self):
        with self.assertRaises(engine.TrustError) as ctx:
            engine.call('trust.privacy_audit', {'files': [{'path': 'a.js', 'content': object()}]})
        self.assertEqual(ctx.exception.code, 'invalid_json')

    def test_bad_timestamp_rejected(self):
        with self.assertRaises(engine.TrustError) as ctx:
            engine.call('trust.fraud_risk', {'events': [
                {'id': 'x', 'ts': 'yesterday', 'kind': 'login', 'outcome': 'fail'}]})
        self.assertEqual(ctx.exception.code, 'invalid_timestamp')


if __name__ == '__main__':
    unittest.main()
