import asyncio
import base64
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.coding_runtime.sandbox import DockerSandbox
class DockerReal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.box=DockerSandbox()
        if not (await self.box.capabilities()).get('ready'):self.skipTest('Docker/image unavailable locally; required on backend')
    async def run_code(self,code,seconds=10):
        return await self.box.run({},'exec.python',{'code':code,'timeout_seconds':seconds},str(uuid4()),deadline_seconds=40)
    async def test_concurrent_large_stdout_stderr_no_deadlock(self):
        r=await self.run_code("import os;os.write(2,b'e'*180000);os.write(1,b'o'*180000)")
        self.assertEqual(r['status'],'completed',r.get('reason'));self.assertEqual(len(r['stdout']),180000);self.assertEqual(len(r['stderr']),180000)
    async def test_overflow_is_bounded_and_failed(self):
        r=await self.run_code("import os;os.write(1,b'o'*900000)")
        self.assertEqual(r['status'],'failed');self.assertEqual(r['reason'],'output_overflow');self.assertLessEqual(len(r['stdout']),512*1024)
    async def test_tmpfs_full_does_not_fill_host_and_is_cleaned(self):
        r=await self.run_code("try:\n open('too-big.bin','wb').write(b'x'*(40*1024*1024))\nexcept OSError as e:\n print(e.errno)\n")
        self.assertEqual(r['status'],'completed',r);self.assertIn('28',r['stdout']);self.assertNotIn('snapshot',r);self.assertTrue(r['changed_state_unknown'])
    async def test_export_rejects_symlink_and_network_is_absent(self):
        r=await self.run_code("import os,socket;os.symlink('/etc/passwd','outside');s=socket.socket();s.settimeout(.5);assert s.connect_ex(('1.1.1.1',443))!=0;print('safe')")
        self.assertEqual(r['status'],'completed',r);self.assertNotIn('outside',r['snapshot']);self.assertIn('safe',r['stdout'])
    async def test_running_container_flags_and_cleanup(self):
        jid=str(uuid4());task=asyncio.create_task(self.box.run({},'exec.shell',{'command':'echo alive; sleep 3','timeout_seconds':5},jid,deadline_seconds=30))
        data=None
        for _ in range(40):
            proc=await asyncio.create_subprocess_exec('/usr/bin/docker','inspect','hms-coding-'+jid,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            raw,_=await proc.communicate()
            if proc.returncode==0:data=json.loads(raw)[0];break
            await asyncio.sleep(.1)
        self.assertIsNotNone(data)
        host=data['HostConfig'];self.assertTrue(host['ReadonlyRootfs']);self.assertEqual(host['NetworkMode'],'none');self.assertEqual(host['LogConfig']['Type'],'none');self.assertEqual(data['Config']['User'],'10000:10000');self.assertEqual(host['CapDrop'],['ALL']);self.assertLessEqual(host['PidsLimit'],64)
        self.assertTrue(all(m['Type']=='volume' or m['Destination']=='/tmp' for m in data['Mounts']))
        self.assertEqual((await task)['status'],'completed')
        proc=await asyncio.create_subprocess_exec('/usr/bin/docker','volume','inspect','hms-coding-vol-'+jid,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL);self.assertNotEqual(await proc.wait(),0)
