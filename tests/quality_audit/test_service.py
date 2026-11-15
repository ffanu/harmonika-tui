import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from aiohttp.test_utils import TestClient,TestServer

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.quality_audit.service import app
from backend.quality_audit.store import Store
from test_store import event


class HTTPBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'q/db',os.urandom(32),os.urandom(32))
        self.ingest='i'*40;self.admin='a'*40
        self.client=TestClient(TestServer(app(self.store,self.ingest,self.admin)));await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close();self.tmp.cleanup()

    def headers(self,admin=False):return {'Authorization':'Bearer '+(self.admin if admin else self.ingest)}

    async def test_auth_domains_and_no_key_query(self):
        self.assertEqual((await self.client.get('/healthz')).status,200)
        for path in ('/traces?token_id=7','/summary','/metrics'):
            self.assertEqual((await self.client.get(path,headers=self.headers())).status,403)
        self.assertEqual((await self.client.post('/ingest',json=event(),headers=self.headers(True))).status,403)
        self.assertEqual((await self.client.get('/summary?token=anything')).status,403)

    async def test_roundtrip_consent_and_metadata_default(self):
        r=await self.client.post('/consent',json={'token_id':7,'granted':True},headers=self.headers(True));self.assertEqual(r.status,200)
        r=await self.client.post('/ingest',json=event(),headers=self.headers());self.assertEqual(r.status,200)
        r=await self.client.get('/traces?token_id=7',headers=self.headers(True));v=await r.json()
        self.assertNotIn('sample',v['traces'][0]);self.assertEqual(r.headers['Cache-Control'],'no-store')
        r=await self.client.get('/traces?token_id=7&include_content=true',headers=self.headers(True));self.assertIn('sample',(await r.json())['traces'][0])
        r=await self.client.get('/traces?token_id=8&include_content=true',headers=self.headers(True));self.assertFalse((await r.json())['traces'])

    async def test_storage_failure_never_echoes_exception_or_payload(self):
        with patch.object(self.store,'ingest',side_effect=RuntimeError('PRIVATE-CANARY')):
            r=await self.client.post('/ingest',json=event(),headers=self.headers())
        self.assertEqual(r.status,503);self.assertNotIn('PRIVATE-CANARY',await r.text())

    async def test_bad_json_duplicate_fields_and_size_limit(self):
        for raw in ('{"token_id":7,"token_id":8}','{"value":NaN}'):
            r=await self.client.post('/ingest',data=raw,headers=self.headers());self.assertEqual(r.status,400)
        r=await self.client.post('/ingest',data='x'*196609,headers=self.headers());self.assertEqual(r.status,413)


if __name__=='__main__':unittest.main()
