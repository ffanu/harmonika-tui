"""Linux browser dependency policy, fully mocked: never executes apt-get."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'runtime/feature_setup.py'
spec=importlib.util.spec_from_file_location('feature_system_deps_fixture',SOURCE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
MISSING={'ok':False,'code':'browser_system_libraries_missing'}
FAILED={'ok':False,'code':'browser_launch_failed'}
READY={'ok':True,'screenshot_bytes':200}

class BrowserSystemDependencies(unittest.TestCase):
    def setup(self, replies, *, uid=0, system='Linux', choice='auto', apt=True):
        self.calls=[]
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve()
        module=root/'browser-runtime/node_modules/playwright'
        def run(argv,**kwargs):
            self.calls.append((argv,kwargs))
            if 'install-deps' in argv:
                config=Path(kwargs['env']['APT_CONFIG'])
                self.assertIn('APT::Get::Upgrade "false"',config.read_text(encoding='utf-8'))
                self.assertLessEqual(kwargs['timeout'],600)
                self.assertEqual(kwargs['env']['DEBIAN_FRONTEND'],'noninteractive')
                self.assertEqual(kwargs['env']['NEEDRESTART_MODE'],'l')
                self.assertEqual(kwargs['env']['NEEDRESTART_SUSPEND'],'1')
                self.assertNotIn('sudo',argv)
            value=replies.pop(0)
            if isinstance(value,BaseException):raise value
            if type(value) is int:return subprocess.CompletedProcess(argv,value,b'',b'PRIVATE_APT_LOG')
            return subprocess.CompletedProcess(argv,0 if value.get('ok') else 1,json.dumps(value).encode(),b'')
        contexts=[patch.object(m,'browser_node',return_value=('fixture-node',False)),
                  patch.object(m,'setup_npm',return_value=(module,'fixture-hash')),
                  patch.object(m,'_run',side_effect=run),patch.object(m.platform,'system',return_value=system),
                  patch.object(m.os,'geteuid',return_value=uid,create=True),
                  patch.object(m.shutil,'which',return_value='/usr/bin/apt-get' if apt else None),
                  patch.dict(os.environ,{'HARMONIKA_INSTALL_SYSTEM_DEPS':choice})]
        for context in contexts:context.start();self.addCleanup(context.stop)
        return root

    def test_ready_browser_never_installs_system_packages(self):
        root=self.setup([READY])
        value=m.setup_browser(root,None)
        self.assertTrue(value['ready']);self.assertFalse(value['system_dependencies_installed'])
        self.assertEqual(len(self.calls),1)

    def test_root_missing_libraries_installs_once_then_requires_real_screenshot(self):
        root=self.setup([MISSING,0,READY])
        value=m.setup_browser(root,None)
        self.assertTrue(value['ready']);self.assertTrue(value['screenshot_verified'])
        self.assertTrue(value['system_dependencies_installed'])
        self.assertEqual([c[0][2:] for c in self.calls if 'install-deps' in c[0]], [['install-deps','chromium']])
        self.assertFalse(any('install' in c[0] for c in self.calls),'no redundant Chromium download')

    def test_missing_browser_download_then_linux_dependencies(self):
        root=self.setup([FAILED,0,MISSING,0,READY])
        self.assertTrue(m.setup_browser(root,None)['ready'])
        self.assertEqual([argv[2] for argv,_ in self.calls if 'install' in argv or 'install-deps' in argv],['install','install-deps'])

    def test_root_optout_never_executes_package_manager(self):
        root=self.setup([MISSING],choice='0')
        with self.assertRaisesRegex(m.SetupError,'browser_system_libraries_missing'):m.setup_browser(root,None)
        self.assertEqual(len(self.calls),1)

    def test_nonroot_auto_leaves_explicit_degraded_status(self):
        root=self.setup([MISSING],uid=1000)
        with self.assertRaises(m.SetupError) as error:m.setup_browser(root,None)
        self.assertEqual(m._failure(error.exception)['status'],'degraded')
        self.assertEqual(len(self.calls),1)

    def test_explicit_request_never_elevates_unprivileged_user(self):
        root=self.setup([MISSING],uid=1000,choice='1')
        with self.assertRaisesRegex(m.SetupError,'privilege_required'):m.setup_browser(root,None)
        self.assertEqual(len(self.calls),1)

    def test_nonlinux_or_missing_apt_never_attempts_package_install(self):
        for system,apt in [('Darwin',True),('Windows',True),('Linux',False)]:
            with self.subTest(system=system),patch.object(m.platform,'system',return_value=system),patch.object(m.os,'geteuid',return_value=0,create=True),patch.object(m.shutil,'which',return_value='/usr/bin/apt-get' if apt else None),patch.dict(os.environ,{'HARMONIKA_INSTALL_SYSTEM_DEPS':'auto'}),patch.object(m,'_run') as run:
                self.assertFalse(m._install_browser_system_dependencies('node',Path('module'),{}))
                run.assert_not_called()

    def test_explicit_request_without_apt_is_not_reported_success(self):
        root=self.setup([MISSING],choice='1',apt=False)
        with self.assertRaisesRegex(m.SetupError,'manager_unavailable'):m.setup_browser(root,None)
        self.assertEqual(len(self.calls),1)

    def test_dependency_success_does_not_hide_other_launch_failure(self):
        root=self.setup([MISSING,0,FAILED])
        with self.assertRaisesRegex(m.SetupError,'browser_launch_failed'):m.setup_browser(root,None)
        self.assertEqual(sum('install-deps' in argv for argv,_ in self.calls),1)

    def test_package_failure_is_retryable_and_keeps_logs_private(self):
        root=self.setup([MISSING,100])
        with self.assertRaises(m.SetupError) as error:m.setup_browser(root,None)
        failure=m._failure(error.exception)
        self.assertFalse(failure['ready']);self.assertTrue(failure['retryable'])
        self.assertEqual(failure['code'],'browser_system_dependencies_install_failed')
        self.assertNotIn('PRIVATE_APT_LOG',json.dumps(failure))

    def test_successful_install_without_successful_probe_is_not_ready(self):
        root=self.setup([MISSING,0,MISSING])
        with self.assertRaisesRegex(m.SetupError,'browser_system_libraries_missing'):m.setup_browser(root,None)
        self.assertEqual(sum('install-deps' in argv for argv,_ in self.calls),1)

    def test_dependency_timeout_is_structured_without_repeating_install(self):
        root=self.setup([MISSING,m.SetupError('feature_command_timeout',True)])
        with self.assertRaises(m.SetupError) as error:m.setup_browser(root,None)
        self.assertTrue(m._failure(error.exception)['retryable'])
        self.assertEqual(sum('install-deps' in argv for argv,_ in self.calls),1)

if __name__=='__main__':unittest.main()
