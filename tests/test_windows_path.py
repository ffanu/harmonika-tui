"""Per-user PATH and alias fixtures; never writes a real registry/profile."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'runtime/tools/windows_path.py'
spec=importlib.util.spec_from_file_location('windows_path_fixture',SOURCE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Store:
    def __init__(self,raw='',kind=2):self.raw=raw;self.kind=kind;self.writes=[]
    def read_path(self):return self.raw,self.kind
    def write_path(self,value,kind):self.raw=value;self.kind=kind;self.writes.append((value,kind))

class WindowsPath(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.bin=self.root/'Private Space %PATH_SENTINEL% & !literal!'/'.local/bin';self.bin.mkdir(parents=True)
        self.wrapper=self.bin/'harmonika.cmd';self.wrapper.write_text('@echo fixture',encoding='utf-8')
        self.local=self.root/'Local';self.roaming=self.root/'Roaming'
        self.apps=self.local/'Microsoft/WindowsApps';self.npm=self.roaming/'npm'
        self.apps.mkdir(parents=True);self.npm.mkdir(parents=True)
        self.env={'LOCALAPPDATA':str(self.local),'APPDATA':str(self.roaming),'USERPROFILE':str(self.bin.parent.parent),
                  'PATH':str(self.apps)+';'+str(self.npm),'PATH_SENTINEL':'SHOULD_NOT_EXPAND_IN_SHIM'}
        self.store=Store('%UNRELATED_VAR%\\tools;;C:\\Other\\bin;',2);self.notifications=0
    def notify(self):self.notifications+=1;return True
    def configure(self,**kwargs):return m.configure_windows_command(self.bin,env=kwargs.pop('env',self.env),store=kwargs.pop('store',self.store),notifier=self.notify,**kwargs)

    def test_preserves_raw_unrelated_entries_registry_kind_and_notifies(self):
        raw=self.store.raw;result=self.configure()
        self.assertTrue(result['path_persisted']);self.assertTrue(result['path_changed']);self.assertTrue(result['environment_notified'])
        self.assertEqual(self.store.raw,str(self.bin)+';'+raw);self.assertEqual(self.store.kind,2);self.assertEqual(self.notifications,1)
        self.assertNotIn(str(self.root),str(result),'mechanical result excludes user paths')

    def test_existing_expanded_quoted_path_is_prepended_once_without_losing_raw_variables(self):
        alias='"%userprofile%\\.LOCAL\\BIN\\"'
        self.store=Store('C:\\Other;'+alias+';'+str(self.bin).upper()+';%KEEP_THIS%\\bin',1)
        result=self.configure();self.assertTrue(result['path_persisted'])
        self.assertEqual(self.store.raw,alias+';C:\\Other;%KEEP_THIS%\\bin');self.assertEqual(self.store.kind,1)
        self.assertEqual(len(self.store.writes),1)
        second=self.configure();self.assertFalse(second['path_changed']);self.assertEqual(len(self.store.writes),1)
        self.assertTrue(second['alias_existing'])

    def test_alias_is_direct_tail_dispatch_and_escapes_only_literal_path_percent(self):
        result=self.configure();self.assertTrue(result['alias_installed']);self.assertTrue(result['stale_parent_path_ready'])
        shim=(self.apps/'harmonika.cmd').read_text(encoding='utf-8')
        self.assertTrue(shim.startswith(m.MANAGED_HEADER+'\n'))
        self.assertIn('setlocal DisableDelayedExpansion',shim)
        self.assertIn('"'+str(self.wrapper).replace('%','%%')+'" %*',shim)
        self.assertNotIn('call ',shim.lower());self.assertNotIn('SHOULD_NOT_EXPAND',shim)
        self.assertEqual((self.apps/'harmonika.cmd').stat().st_nlink,1)

    def test_original_path_precedes_bootstrap_mutation(self):
        env={**self.env,'HARMONIKA_ORIGINAL_PATH':str(self.apps),'PATH':str(self.bin)+';'+self.env['PATH']}
        self.assertTrue(self.configure(env=env)['alias_installed'])
        env['HARMONIKA_ORIGINAL_PATH']='C:\\Unrelated'
        result=self.configure(env=env);self.assertEqual(result['status'],'shell_refresh_required');self.assertFalse(result['stale_parent_path_ready'])

    def test_canonical_bin_already_in_original_path_needs_no_alias(self):
        result=self.configure(env={**self.env,'HARMONIKA_ORIGINAL_PATH':'"'+str(self.bin).upper()+'\\"'})
        self.assertEqual(result['status'],'ready');self.assertEqual(result['alias']['status'],'not_needed')
        self.assertFalse((self.apps/'harmonika.cmd').exists())

    def test_unknown_existing_alias_preserved_and_fallback_reported_honestly(self):
        original=b'@echo user-owned-unknown\r\n';(self.apps/'harmonika.cmd').write_bytes(original)
        result=self.configure()
        self.assertEqual((self.apps/'harmonika.cmd').read_bytes(),original)
        self.assertTrue((self.npm/'harmonika.cmd').is_file());self.assertTrue(result['alias_installed'])
        self.assertFalse(result['stale_parent_path_ready']);self.assertEqual(result['alias']['status'],'shadowed')

    def test_unknown_other_command_extension_is_not_overwritten(self):
        (self.apps/'harmonika.exe').write_bytes(b'synthetic')
        result=self.configure()
        self.assertFalse((self.apps/'harmonika.cmd').exists());self.assertTrue((self.npm/'harmonika.cmd').is_file())
        self.assertFalse(result['stale_parent_path_ready'])

    def test_unknown_candidate_directories_are_never_created(self):
        unsafe=self.root/'System32';result=self.configure(candidates=[unsafe])
        self.assertTrue(result['path_persisted']);self.assertFalse(result['alias_installed']);self.assertFalse(unsafe.exists())

    def test_missing_expired_or_unsafe_wrapper_does_not_persist_path(self):
        self.wrapper.unlink();result=self.configure()
        self.assertFalse(result['path_persisted']);self.assertEqual(self.store.writes,[])
        self.wrapper.mkdir();self.assertFalse(self.configure()['path_persisted'])

    def test_reparse_file_conflict_is_preserved(self):
        target=self.apps/'harmonika.cmd';target.write_bytes(b'synthetic reparse fixture')
        original=m._linklike
        with patch.object(m,'_linklike',side_effect=lambda path:path==target or original(path)):
            result=self.configure()
        self.assertEqual(target.read_bytes(),b'synthetic reparse fixture');self.assertEqual(result['alias']['location'],'npm')

    def test_managed_alias_updates_atomically_but_unknown_race_is_preserved(self):
        self.configure();alias=self.apps/'harmonika.cmd';alias.write_bytes((m.MANAGED_HEADER+'\r\nold fixture\r\n').encode())
        self.assertTrue(self.configure()['alias']['created']);self.assertIn(str(self.wrapper).replace('%','%%').encode(),alias.read_bytes())
        alias.unlink();link=os.link
        def race(source,target):
            Path(target).write_bytes(b'concurrent unknown command')
            raise FileExistsError()
        with patch.object(m.os,'link',side_effect=race):result=self.configure(candidates=[self.apps])
        self.assertEqual(alias.read_bytes(),b'concurrent unknown command');self.assertFalse(result['alias_installed'])
        self.assertEqual(list(self.apps.glob('.harmonika-shim-*.tmp')),[])

    def test_write_failure_and_unsupported_registry_kind_do_not_claim_readiness(self):
        with patch.object(self.store,'write_path',side_effect=PermissionError()):
            result=self.configure();self.assertFalse(result['path_persisted']);self.assertEqual(result['status'],'degraded')
        self.store=Store('fixture',7);result=self.configure();self.assertFalse(result['path_persisted']);self.assertEqual(self.store.writes,[])

    def test_canonical_name_expansion_does_not_require_identical_spelling(self):
        canonical=self.root/'Long Canonical Profile'/'.local/bin';canonical.mkdir(parents=True)
        (canonical/'harmonika.cmd').write_text('@echo fixture',encoding='utf-8')
        real_resolve=Path.resolve
        def resolve(path,*args,**kwargs):
            return canonical if path==self.bin else real_resolve(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            self.assertEqual(m._canonical_bindir(self.bin),canonical)

    def test_reparse_ancestor_refused_before_canonical_resolution(self):
        actual=m._linklike
        with patch.object(m,'_linklike',side_effect=lambda path:path==self.bin.parent or actual(path)):
            result=self.configure()
        self.assertFalse(result['path_persisted']);self.assertEqual(self.store.writes,[])

    def test_merge_uses_semicolon_not_posix_path_separator(self):
        raw='C:\\Tools;%PRIVATE_ROOT%\\bin;https://example.invalid'
        self.assertEqual(m.merge_user_path(raw,self.bin,self.env),str(self.bin)+';'+raw)

if __name__=='__main__':unittest.main()
