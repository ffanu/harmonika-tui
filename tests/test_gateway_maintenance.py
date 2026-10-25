import importlib.util
from pathlib import Path
import unittest

s=importlib.util.spec_from_file_location('gateway_maintenance',Path(__file__).resolve().parents[1]/'scripts/restart-tui-gateway.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class FakeSystem:
    target='2026.10.24.13'
    def __init__(self,preflight_error=False,busy=False,verify_error=False,rollback_error=False):
        self.events=[];self.preflight_error=preflight_error;self.busy=busy;self.verify_error=verify_error;self.rollback_error=rollback_error;self.idle_calls=0
    def preflight(self,rollback):
        self.events.append('preflight')
        if self.preflight_error:raise m.MaintenanceError('invalid_signature')
        return {'signature':'verified'}
    def wait_idle(self):
        self.events.append('idle');self.idle_calls+=1
        if self.busy or (self.rollback_error and self.idle_calls>1):raise m.MaintenanceError('busy')
    def switch(self,release):self.events.append(('switch',release))
    def restart(self):self.events.append('restart')
    def wait_health(self,contract):self.events.append(('health',contract))
    def verify(self):
        self.events.append('verify')
        if self.verify_error:raise m.MaintenanceError('native_smoke_failed')
        return {'native':'passed'}

class Maintenance(unittest.TestCase):
    def test_verified_idle_restart_before_checks(self):
        fake=FakeSystem();result=m.Maintenance(fake).apply('2026.10.24.12')
        self.assertTrue(result['ok']);self.assertEqual(fake.events,['preflight','idle',('switch',fake.target),'restart',('health',1),'verify'])
    def test_signature_failure_does_not_restart(self):
        fake=FakeSystem(preflight_error=True)
        with self.assertRaisesRegex(m.MaintenanceError,'invalid_signature'):m.Maintenance(fake).apply('2026.10.24.12')
        self.assertEqual(fake.events,['preflight'])
    def test_busy_initial_gateway_never_restarted(self):
        fake=FakeSystem(busy=True)
        with self.assertRaises(m.MaintenanceError):m.Maintenance(fake).apply('2026.10.24.12')
        self.assertNotIn('restart',fake.events)
    def test_verification_failure_restores_previous_service_release(self):
        fake=FakeSystem(verify_error=True)
        with self.assertRaisesRegex(m.MaintenanceError,'activation_failed_rolled_back'):m.Maintenance(fake).apply('2026.10.24.12')
        self.assertEqual(fake.events[-4:],['idle',('switch','2026.10.24.12'),'restart',('health',None)])
    def test_busy_rollback_does_not_force_restart(self):
        fake=FakeSystem(verify_error=True,rollback_error=True)
        with self.assertRaisesRegex(m.MaintenanceError,'rollback_needs_attention'):m.Maintenance(fake).apply('2026.10.24.12')
        self.assertEqual(fake.events.count('restart'),1)
    def test_draining_includes_close_wait_and_upstream_connections(self):
        states=m.connection_states('p123\nf4\nn127.0.0.1:18184\nTST=LISTEN\nf5\nTST=ESTABLISHED\nf6\nTST=CLOSE_WAIT\n')
        self.assertEqual(states,['ESTABLISHED','CLOSE_WAIT']);self.assertEqual(m.connection_states('TST=LISTEN'),[])
    def test_native_env_isolates_config_and_global_data(self):
        base={'HOME':'/fixture','OTHER':'preserved'}
        env=m.native_environment("env=dict(os.environ,COMPAT_GLOBAL_CONFIG='x',COMPAT_GLOBAL_DATA='y')",Path('/tmp/smoke/config'),base)
        self.assertEqual(env['COMPAT_GLOBAL_CONFIG'],'/tmp/smoke/config');self.assertEqual(env['COMPAT_GLOBAL_DATA'],'/tmp/smoke/global-data')
        self.assertNotIn('HARMONIKA_GLOBAL_CONFIG',base);self.assertEqual(env['OTHER'],'preserved')
