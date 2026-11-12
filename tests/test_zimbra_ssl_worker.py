import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from backend.zimbra import ssl_worker as w

CFG = {'cert_name': 'mail.example.test', 'hostname': 'mail.example.test', 'service_control_approved': False}


class WorkerFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        with patch.object(w.pwd, 'getpwnam', return_value=Mock(pw_uid=os.getuid(), pw_gid=os.getgid())):
            self.worker = w.Worker(copy.deepcopy(CFG))
        self.worker.directory = self.directory
        self.worker.marker = self.directory / 'reconciliation-required.json'
        self.worker.receipt = self.directory / 'staging.json'
        self.snapshot = {'fingerprint': 'a', 'deployed': 'a', 'served': {p: 'a' for p in w.PORTS},
                         'due': False, 'sans': ['mail.example.test', 'alias.example.test'],
                         'config_sha256': 'config-hash', 'paths': {}}
        self.worker.snapshot = Mock(return_value=self.snapshot)

    def tearDown(self): self.temp.cleanup()

    def enable(self): self.worker.config['service_control_approved'] = True

    def receipt(self):
        self.worker.receipt.write_text(json.dumps({'cert_name': CFG['cert_name'], 'sans': self.snapshot['sans'],
                                                  'config_sha256': 'config-hash', 'at': time.time(), 'worker_version': w.VERSION}))

    def test_not_due_and_unchanged_never_controls_services(self):
        self.worker.challenge = Mock(); self.worker.deploy = Mock()
        self.assertEqual(self.worker.run('renew'), 'unchanged')
        self.worker.challenge.assert_not_called(); self.worker.deploy.assert_not_called()

    def test_inspect_does_not_challenge_even_when_due(self):
        self.snapshot['due'] = True; self.worker.challenge = Mock()
        self.assertEqual(self.worker.run('inspect'), 'inspected'); self.worker.challenge.assert_not_called()

    def test_due_requires_explicit_boolean_authorization(self):
        self.snapshot['due'] = True; self.worker.challenge = Mock()
        with self.assertRaises(w.Blocked): self.worker.run('renew')
        self.worker.challenge.assert_not_called()
        with self.assertRaises(w.Blocked): w.validate_config({**CFG, 'service_control_approved': 'true'})

    def test_staging_never_deploys(self):
        self.enable(); self.worker.challenge = Mock(); self.worker.deploy = Mock()
        self.assertEqual(self.worker.run('dry-run'), 'staging_verified_no_deploy')
        self.worker.challenge.assert_called_once_with(True, self.snapshot); self.worker.deploy.assert_not_called()

    def test_pending_deployment_is_reconciled_without_new_issuance(self):
        self.enable(); self.snapshot['deployed'] = 'old'; self.receipt()
        self.worker.challenge = Mock(); self.worker.deploy = Mock(); self.worker.save_receipt = Mock()
        with patch.object(w, 'read_regular', side_effect=lambda p, *a, **kw: p.read_bytes()):
            self.assertEqual(self.worker.run('renew'), 'deployed_verified')
        self.worker.challenge.assert_not_called(); self.worker.deploy.assert_called_once_with({}, self.snapshot['sans'])

    def test_endpoint_drift_does_not_trigger_blind_restart(self):
        self.enable(); self.receipt(); self.snapshot['served'][993] = 'old'
        self.worker.deploy = Mock()
        with patch.object(w, 'read_regular', side_effect=lambda p, *a, **kw: p.read_bytes()):
            with self.assertRaisesRegex(w.Blocked, 'endpoint_drift'): self.worker.run('renew')
        self.worker.deploy.assert_not_called()

    def test_unknown_previous_outcome_blocks_every_followup(self):
        self.worker.marker.write_text('{}')
        with self.assertRaisesRegex(w.Blocked, 'reconciliation'): self.worker.run('renew')
        self.worker.snapshot.assert_not_called()

    def test_changed_config_staging_receipt_blocks_production(self):
        self.enable(); self.snapshot['due'] = True; self.receipt()
        self.snapshot['config_sha256'] = 'changed'; self.worker.challenge = Mock()
        with patch.object(w, 'read_regular', side_effect=lambda p, *a, **kw: p.read_bytes()):
            with self.assertRaisesRegex(w.Blocked, 'staging_receipt'): self.worker.run('renew')
        self.worker.challenge.assert_not_called()

    def test_stable_config_renewal_does_not_require_manual_monthly_reapproval(self):
        self.enable(); self.receipt(); self.snapshot['deployed'] = 'old'
        data=json.loads(self.worker.receipt.read_text());data['at']-=100*86400
        self.worker.receipt.write_text(json.dumps(data));self.worker.deploy=Mock();self.worker.save_receipt=Mock()
        with patch.object(w, 'read_regular', side_effect=lambda p, *a, **kw: p.read_bytes()):
            self.assertEqual(self.worker.run('renew'), 'deployed_verified')
        self.worker.save_receipt.assert_called_once_with(self.snapshot)

    def test_mutation_known_failure_clears_marker_unknown_keeps_marker(self):
        self.enable()
        with patch.object(w, 'put', side_effect=lambda p, data: p.write_bytes(data)):
            for error, exists in ((w.Failed(), False), (w.Uncertain(), True)):
                with patch.object(w, 'command', side_effect=error):
                    with self.assertRaises(type(error)): self.worker.mutate(['/fixed'])
                self.assertEqual(self.worker.marker.exists(), exists)

    def challenge_setup(self, certbot_error):
        self.enable()
        self.worker.vendor = Mock(side_effect=lambda *a, **k: b'proxy is running' if a[1] == 'status' else b'ok')
        self.worker.mutate = Mock(side_effect=certbot_error)

    def test_confirmed_certbot_failure_always_recovers_proxy(self):
        self.challenge_setup(w.Failed('acme_failure'))
        with patch.object(w, 'inspect_config', return_value='config-hash'), patch.object(w, 'command', return_value=b''), patch.object(w.socket, 'create_connection', side_effect=OSError):
            with self.assertRaises(w.Failed): self.worker.challenge(False, self.snapshot)
        self.assertEqual(self.worker.vendor.call_args.args[:2], ('zmproxyctl', 'start'))
        argv = self.worker.mutate.call_args.args[0]
        self.assertNotIn('--force-renewal', argv); self.assertIn('--no-directory-hooks', argv)

    def test_unknown_certbot_does_not_start_proxy_into_unknown_port_owner(self):
        def uncertain(*a):
            self.worker.marker.write_text('{}'); raise w.Uncertain()
        self.challenge_setup(None); self.worker.mutate.side_effect = uncertain
        with patch.object(w, 'inspect_config', return_value='config-hash'), patch.object(w, 'command', return_value=b''), patch.object(w.socket, 'create_connection', side_effect=OSError):
            with self.assertRaises(w.Uncertain): self.worker.challenge(False, self.snapshot)
        self.assertFalse(any(c.args[:2] == ('zmproxyctl', 'start') for c in self.worker.vendor.mock_calls))

    def test_confirmed_stop_failure_also_attempts_proxy_recovery(self):
        self.enable()
        def vendor(binary, op, **kwargs):
            if op == 'status': return b'proxy is running'
            if op == 'stop': raise w.Failed()
        self.worker.vendor = Mock(side_effect=vendor)
        with patch.object(w, 'inspect_config', return_value='config-hash'), patch.object(w, 'command', return_value=b''):
            with self.assertRaises(w.Failed): self.worker.challenge(True, self.snapshot)
        self.assertEqual(self.worker.vendor.call_args.args[:2], ('zmproxyctl', 'start'))

    def test_failed_proxy_recovery_retains_marker_for_next_run(self):
        self.enable()
        def vendor(binary,op,**kwargs):
            if op=='status':return b'proxy is running'
            if op=='start':raise w.Failed('start_failed')
        self.worker.vendor=Mock(side_effect=vendor);self.worker.mutate=Mock(side_effect=w.Failed('acme_failed'))
        with patch.object(w,'inspect_config',return_value='config-hash'),patch.object(w,'command',return_value=b''), \
             patch.object(w.socket,'create_connection',side_effect=OSError),patch.object(w,'put',side_effect=lambda p,b:p.write_bytes(b)):
            with self.assertRaises(w.Failed):self.worker.challenge(False,self.snapshot)
        self.assertTrue(self.worker.marker.exists())
        with self.assertRaisesRegex(w.Blocked,'reconciliation'):self.worker.run('renew')

    def test_staging_receipt_binds_config_after_certbot_not_before(self):
        self.enable();self.worker.vendor=Mock(return_value=b'proxy is running');self.worker.mutate=Mock();self.worker.save_receipt=Mock()
        with patch.object(w,'inspect_config',side_effect=['config-hash','post-certbot-hash']),patch.object(w,'command',return_value=b''), \
             patch.object(w.socket,'create_connection',side_effect=OSError),patch.object(w,'served_fingerprints',return_value=self.snapshot['served']):
            self.worker.challenge(True,self.snapshot)
        self.assertEqual(self.worker.save_receipt.call_args.args[0]['config_sha256'],'post-certbot-hash')

    def test_wildcard_matches_one_label_only_and_no_cn_fallback(self):
        decoded={'subjectAltName':[('DNS','*.example.test')]}
        w.match_hostname(decoded,'mail.example.test')
        for host in ('example.test','a.b.example.test','evil.test'):
            with self.assertRaises(w.Blocked):w.match_hostname(decoded,host)
        with self.assertRaises(w.Blocked):w.match_hostname({'subject':[[('commonName','mail.example.test')]]},'mail.example.test')

    def test_sqlite_firewall_has_exact_four_fields_and_never_exception_text(self):
        w.record(self.directory, 'PRIVATE_SENTINEL', time.monotonic())
        import sqlite3
        con = sqlite3.connect(self.directory / 'mechanics.sqlite3')
        self.assertEqual([r[1] for r in con.execute('PRAGMA table_info(events)')], ['timestamp', 'skill_id', 'error_type', 'latency'])
        self.assertEqual(con.execute('SELECT error_type FROM events').fetchone()[0], 'internal_error')
        con.close()
        self.assertNotIn(b'PRIVATE_SENTINEL', (self.directory / 'mechanics.sqlite3').read_bytes())

    def test_configuration_rejects_shell_paths_and_extra_keys(self):
        for value in ('../../etc/passwd', 'mail.test;reboot', 'UPPER.test', '-bad.test'):
            with self.assertRaises(w.Blocked): w.validate_config({**CFG, 'cert_name': value})
        with self.assertRaises(w.Blocked): w.validate_config({**CFG, 'command': 'any'})

    def test_private_file_rejects_symlink_and_group_write(self):
        source = self.directory / 'key'; source.write_bytes(b'private'); source.chmod(0o600)
        link = self.directory / 'link'; link.symlink_to(source)
        with self.assertRaises(OSError): w.read_regular(link, owners=(os.getuid(),))
        source.chmod(0o660)
        with self.assertRaises(w.Blocked): w.read_regular(source, key=True, owners=(os.getuid(),))

    def deployment(self, failure=None):
        self.enable(); stage = self.directory / 'stage'; stage.mkdir()
        self.worker.stage = Mock(return_value=stage)
        self.worker.clean_stage = Mock(); self.worker.verify_served = Mock()
        def vendor(binary, *args, **kwargs):
            if failure and binary == 'zmcertmgr' and self.worker.vendor.call_count == 1: raise failure
            return b'ok'
        self.worker.vendor = Mock(side_effect=vendor)
        def fake_put(path, data, *args):
            if path == self.worker.marker: path.write_bytes(data)
        return stage, fake_put

    def test_confirmed_deployment_failure_uses_vendor_rollback_and_verifies(self):
        stage, writer = self.deployment(w.Failed())
        with patch.object(w, 'put', side_effect=writer), patch.object(w, 'read_regular', return_value=b'material'), \
             patch.object(w, 'certificate', return_value={'fingerprint': 'old'}):
            with self.assertRaisesRegex(w.Failed, 'rolled_back'): self.worker.deploy({}, ['mail.example.test'])
        self.assertEqual([c.args[:2] for c in self.worker.vendor.mock_calls],
                         [('zmcertmgr', 'deploycrt'), ('zmcertmgr', 'deploycrt'), ('zmcontrol', 'restart')])
        self.worker.verify_served.assert_called_once_with('old')
        self.assertFalse(self.worker.marker.exists())

    def test_uncertain_deploy_preserves_private_stage_and_blocks_retry(self):
        stage, writer = self.deployment(w.Uncertain())
        with patch.object(w, 'put', side_effect=writer), patch.object(w, 'read_regular', return_value=b'material'), \
             patch.object(w, 'certificate', return_value={'fingerprint': 'old'}):
            with self.assertRaises(w.Uncertain): self.worker.deploy({}, ['mail.example.test'])
        self.assertEqual(self.worker.vendor.call_count, 1)
        self.worker.clean_stage.assert_not_called(); self.assertTrue(self.worker.marker.exists())

    def test_successful_deploy_verifies_every_endpoint_before_clearing_marker(self):
        stage, writer = self.deployment()
        with patch.object(w, 'put', side_effect=writer), patch.object(w, 'read_regular', return_value=b'material'), \
             patch.object(w, 'certificate', return_value={'fingerprint': 'new'}):
            self.worker.deploy({'cert': Path('new.pem')}, ['mail.example.test'])
        self.worker.verify_served.assert_called_once_with('new')
        self.assertFalse(self.worker.marker.exists()); self.worker.clean_stage.assert_called_once_with(stage)

    def test_failed_material_validation_cannot_install_or_restart(self):
        self.enable(); self.worker.stage = Mock(side_effect=w.Blocked('key_mismatch'))
        self.worker.vendor = Mock()
        with self.assertRaises(w.Blocked): self.worker.deploy({}, ['mail.example.test'])
        self.worker.vendor.assert_not_called(); self.assertFalse(self.worker.marker.exists())


class RealCertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.base = Path(cls.temp.name)
        def openssl(*args): subprocess.run(['openssl', *map(str, args)], cwd=cls.base, check=True, capture_output=True)
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem',
                '-days', '40', '-subj', '/CN=Test Root')
        (cls.base / 'ext').write_text('subjectAltName=DNS:mail.example.test,DNS:alias.example.test\nextendedKeyUsage=serverAuth\n')
        for kind in ('rsa', 'ec'):
            args = ['req', '-new', '-nodes', '-keyout', kind + '.key', '-out', kind + '.csr', '-subj', '/CN=mail.example.test']
            args += ['-newkey', 'rsa:2048'] if kind == 'rsa' else ['-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256']
            openssl(*args)
            openssl('x509', '-req', '-in', kind + '.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial',
                    '-out', kind + '.pem', '-days', '20', '-extfile', 'ext')

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def verify(self, key, cert, sans=None):
        actual = w.command
        def fixture_trust(argv, **kwargs):
            argv = [str(self.base / 'ca.pem') if a == '/etc/ssl/certs/ca-certificates.crt' else a for a in argv]
            return actual(argv, **kwargs)
        with patch.object(w, 'command', side_effect=fixture_trust):
            return w.verify_material(self.base / key, self.base / cert, self.base / 'ca.pem',
                                     'mail.example.test', sans or ['mail.example.test', 'alias.example.test'])

    def test_actual_openssl_rsa_and_ec_spki_matching_and_trust(self):
        for kind in ('rsa', 'ec'):
            self.assertEqual(len(self.verify(kind + '.key', kind + '.pem')['fingerprint']), 64)

    def test_real_key_mismatch_is_rejected(self):
        with self.assertRaisesRegex(w.Blocked, 'key_mismatch'): self.verify('rsa.key', 'ec.pem')

    def test_san_loss_blocks_deployment(self):
        with self.assertRaisesRegex(w.Blocked, 'san_changed'):
            self.verify('rsa.key', 'rsa.pem', ['missing.example.test'])

    def test_untrusted_self_signed_certificate_not_accepted_for_production(self):
        with self.assertRaises(w.Failed):
            w.command(['/usr/bin/openssl', 'verify', '-CAfile', str(self.base / 'rsa.pem'), str(self.base / 'ec.pem')])


if __name__ == '__main__': unittest.main()
