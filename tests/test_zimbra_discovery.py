import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.zimbra.build_discovery import build,render
from backend.engine_registry.discovery import Discovery,SanitizedError


class ZimbraDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_isolated_registry_loads_and_invokes_qa_and_rejects_private_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary).resolve();root=base/'skills';root.mkdir()
            module=root/'zimbra-qa';build(module)
            manifest=json.loads((module/'manifest.json').read_text())
            self.assertEqual(manifest['module_sha256'],hashlib.sha256(render()).hexdigest())
            engine=Discovery(root,base/'discovery.sqlite3')
            try:
                summary=await engine.refresh();self.assertEqual(summary['valid'],1)
                descriptor=engine.descriptors()[0]
                self.assertEqual(descriptor['id'],'zimbra.qa');self.assertTrue(descriptor['hash_verified'])
                index=await engine.execute('zimbra.qa',{});self.assertEqual(len(index['topics']),24)
                result=await engine.execute('zimbra.qa',{'topic':'stats_orphan'})
                self.assertEqual(result['version'],'1.4.3');self.assertFalse(result['grants_authorization'])
                with self.assertRaises(SanitizedError):
                    await engine.execute('zimbra.qa',{'topic':'index','chat_history':'PRIVATE_SENTINEL'})
            finally:engine.close()


if __name__=='__main__':unittest.main()
