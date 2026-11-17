"""Private SSH files: cross-platform policy plus real native filesystem checks.

All fixtures are synthetic and local. No user's SSH config or keys are accessed.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('portable_private_files', ROOT/'runtime/tools/private_files.py')
files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(files)
USER = 'S-1-5-21-100-200-300-1001'
EVERYONE = 'S-1-1-0'

def snapshot():
    return {'current_user': USER, 'owner': USER, 'dacl_present': True,
            'entries': [{'type':'allow', 'flags':0, 'mask':0x1f01ff, 'sid':USER},
                        {'type':'allow', 'flags':0, 'mask':0x1f01ff, 'sid':'S-1-5-18'}]}

class WindowsACLPolicy(unittest.TestCase):
    def test_current_user_system_and_administrators(self):
        value = snapshot()
        value['entries'].append({'type':'allow','flags':0,'mask':0x1f01ff,'sid':'S-1-5-32-544'})
        for owner in (USER, 'S-1-5-18', 'S-1-5-32-544'):
            value['owner'] = owner
            self.assertTrue(files.acl_is_private(value))

    def test_broad_groups_cannot_read_private_material(self):
        for sid in (EVERYONE, 'S-1-5-11', 'S-1-5-32-545', 'S-1-5-21-100-200-300-1002'):
            value = snapshot()
            value['entries'].append({'type':'allow','flags':0,'mask':0x120089,'sid':sid})
            self.assertFalse(files.acl_is_private(value))
            self.assertTrue(files.acl_is_private(value, private=False))

    def test_public_known_hosts_must_not_be_writable(self):
        for mask in (2,4,0x10,0x40,0x100,0x10000,0x40000,0x80000,0x10000000,0x40000000,0x01000000,0x02000000):
            value = snapshot()
            value['entries'].append({'type':'allow','flags':0,'mask':mask,'sid':EVERYONE})
            self.assertFalse(files.acl_is_private(value, private=False), hex(mask))

    def test_null_empty_untrusted_and_uninterpreted_acl_fail_closed(self):
        invalid = []
        for key, value in [('dacl_present',False),('entries',[]),('owner',EVERYONE),('current_user','invalid')]:
            data=snapshot();data[key]=value;invalid.append(data)
        for entry in ({'type':'object_allow','mask':1,'flags':0,'sid':USER},
                      {'type':'allow','mask':True,'flags':0,'sid':USER},
                      {'type':'allow','mask':1,'flags':256,'sid':USER}):
            data=snapshot();data['entries'].append(entry);invalid.append(data)
        for value in invalid:
            self.assertFalse(files.acl_is_private(value))

    def test_inherit_only_and_deny_do_not_grant_access(self):
        value = snapshot()
        value['entries'].extend([{'type':'allow','flags':8,'mask':0x1f01ff,'sid':EVERYONE},
                                 {'type':'deny','flags':0,'mask':0x1f01ff,'sid':EVERYONE}])
        before=copy.deepcopy(value)
        self.assertTrue(files.acl_is_private(value))
        self.assertEqual(value,before)

    def test_reparse_attribute_is_linklike(self):
        class Stat:
            st_mode=0o100600
            st_file_attributes=0x400
        with patch.object(Path,'lstat',return_value=Stat()):
            self.assertTrue(files.is_linklike('synthetic'))

def acl_mechanics(value):
    roles={'S-1-5-18':'system','S-1-5-32-544':'administrators','S-1-1-0':'everyone','S-1-5-32-545':'users','S-1-5-11':'authenticated_users',value.get('current_user'):'current_user'}
    return {'owner_role':roles.get(value.get('owner'),'other'),'dacl_present':value.get('dacl_present'),
            'entries':[{k:e.get(k) for k in ('type','flags','mask')} | {'role':roles.get(e.get('sid'),'other')} for e in value.get('entries',[])]}

class NativePrivateFiles(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.file=self.root/'synthetic-key'
        self.file.write_text('synthetic fixture; not a private key')
        self.file.chmod(0o600)
        if os.name=='nt':
            self.user=files.windows_security(self.file)['current_user']
            self.icacls('/inheritance:r', '/grant:r', '*'+self.user+':F', '*S-1-5-18:F', '*S-1-5-32-544:F')
            # Temp files can retain an explicit logon/default-DACL ACE even
            # after inheritance is removed. Restrict only this synthetic file.
            trusted=files.TRUSTED_SYSTEM_SIDS | {self.user}
            untrusted={entry['sid'] for entry in files.windows_security(self.file)['entries']
                       if entry['type']=='allow' and entry['sid'] not in trusted}
            for sid in sorted(untrusted):self.icacls('/remove:g','*'+sid)
            actual=files.windows_security(self.file)
            self.assertTrue(files.acl_is_private(actual),json.dumps(acl_mechanics(actual)))

    def icacls(self, *args):
        result=subprocess.run(['icacls.exe', str(self.file), *args],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,'synthetic fixture ACL setup failed')

    def test_private_file_and_isolated_subprocess(self):
        before=files.windows_security(self.file) if os.name=='nt' else self.file.stat().st_mode
        if os.name=='nt':self.assertTrue(files.acl_is_private(before),json.dumps(acl_mechanics(before)))
        self.assertEqual(files.private_file(self.file),self.file)
        script='import importlib.util,sys;s=importlib.util.spec_from_file_location("p",sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(m.private_file(sys.argv[2]).name)'
        result=subprocess.run([sys.executable,'-I','-c',script,str(ROOT/'runtime/tools/private_files.py'),str(self.file)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),self.file.name)
        after=files.windows_security(self.file) if os.name=='nt' else self.file.stat().st_mode
        self.assertEqual(before,after,'read validation must not modify existing key permissions')

    def test_bounds_relative_and_traversal(self):
        for path in ('relative-file',self.root/'..'/self.root.name/self.file.name):
            with self.assertRaises(files.UnsafeFile):files.private_file(path)
        with self.file.open('wb') as stream:stream.truncate(1024*1024+1)
        with self.assertRaises(files.UnsafeFile):files.private_file(self.file)

    def test_public_readable_config_rejected_known_hosts_allowed(self):
        if os.name=='nt':self.icacls('/grant','*S-1-1-0:R')
        else:self.file.chmod(0o644)
        with self.assertRaises(files.UnsafeFile):files.private_file(self.file)
        if os.name=='nt':
            value=files.windows_security(self.file)
            self.assertTrue(files.acl_is_private(value,private=False),json.dumps(acl_mechanics(value)))
        self.assertEqual(files.private_file(self.file,private=False),self.file)

    def test_public_writable_known_hosts_rejected(self):
        if os.name=='nt':self.icacls('/grant','*S-1-1-0:W')
        else:self.file.chmod(0o666)
        with self.assertRaises(files.UnsafeFile):files.private_file(self.file,private=False)

    def test_symlink_parent_rejected(self):
        target=self.root/'target';target.mkdir()
        (target/'config').write_text('fixture');(target/'config').chmod(0o600)
        link=self.root/'link'
        if os.name=='nt':
            # Junctions are reparse points and do not require Developer Mode.
            value=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(target)],capture_output=True,timeout=10)
            self.assertEqual(value.returncode,0,'synthetic junction setup failed')
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        else:link.symlink_to(target,target_is_directory=True)
        with self.assertRaises(files.UnsafeFile):files.private_file(link/'config')

    @unittest.skipUnless(os.name=='nt','native Windows security API')
    def test_actual_windows_acl_security_query_failure(self):
        with patch.object(files,'windows_security',side_effect=files.UnsafeFile('windows_security_unavailable')):
            with self.assertRaises(files.UnsafeFile):files.private_file(self.file)

class ConnectorWiring(unittest.TestCase):
    def test_shared_feature_interpreter_preferred(self):
        source=(ROOT/'runtime/tools/ssh-mcp.cjs').read_text()
        self.assertIn('process.env.HARMONIKA_SSH_PYTHON||process.env.HARMONIKA_PYTHON||',source)

    def test_runtime_worker_isolated_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            area=Path(temporary).resolve()
            value=subprocess.run([sys.executable,'-I',str(ROOT/'runtime/tools/ssh_connector.py'),'invoke'],input=json.dumps({'skill_id':'ssh.connect','input':{'target':'not-configured'}}),capture_output=True,text=True,timeout=10,env={**os.environ,'HARMONIKA_SSH_CONFIG':str(area/'nonexistent-fixture-file'),'HARMONIKA_SSH_STATE_DIR':str(area/'state')})
        self.assertEqual(value.returncode,0,value.stderr)
        self.assertFalse(json.loads(value.stdout)['ok'])

if __name__=='__main__':unittest.main()
