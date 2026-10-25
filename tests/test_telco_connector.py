import asyncio,json,unittest
from backend.telco_engine.connector_contract import ReadConnector
from backend.telco_engine.common import DomainError,now_iso
class Content:
 def __init__(self,value):self.value=value
 async def iter_chunked(self,n):yield self.value
class Response:
 def __init__(self,value,status=200):self.status=status;self.content=Content(value)
 async def __aenter__(self):return self
 async def __aexit__(self,*a):return None
class Session:
 def __init__(self,value,status=200):self.value=value;self.status=status;self.calls=[]
 def post(self,url,**kw):self.calls.append((url,kw));return Response(self.value,self.status)
class Connector(unittest.IsolatedAsyncioTestCase):
 def payload(self,**changes):return json.dumps({'schema_version':1,'tenant_id':'a'*64,'skill_id':'olt.optical.power','request_id':'fixture','status':'completed','data':{'rx_dbm':-20},'source':'synthetic-fixture','observed_at':now_iso(),**changes}).encode()
 def adapter(self,session):return ReadConnector('https://connector.example','a'*64,['olt.optical.power'],'s'*64,session)
 async def test_verified_shape_and_tenant(self):
  s=Session(self.payload());r=await self.adapter(s).execute('a'*64,'olt.optical.power',{'resource_id':'ont1'},'fixture');self.assertEqual(r['data']['rx_dbm'],-20);self.assertFalse(r['changes_applied']);self.assertFalse(s.calls[0][1]['allow_redirects'])
 async def test_wrong_actor_never_contacts_peer(self):
  s=Session(self.payload())
  with self.assertRaises(DomainError):await self.adapter(s).execute('b'*64,'olt.optical.power',{},'fixture')
  self.assertFalse(s.calls)
 async def test_peer_cannot_switch_tenant(self):
  with self.assertRaises(DomainError):await self.adapter(Session(self.payload(tenant_id='b'*64))).execute('a'*64,'olt.optical.power',{},'fixture')
 async def test_redirect_and_non200_refused(self):
  with self.assertRaises(DomainError):await self.adapter(Session(self.payload(),302)).execute('a'*64,'olt.optical.power',{},'fixture')
 async def test_stale_evidence_refused(self):
  with self.assertRaises(DomainError):await self.adapter(Session(self.payload(observed_at='2000-01-01T00:00:00Z'))).execute('a'*64,'olt.optical.power',{},'fixture')
 async def test_wrong_requestid_refused(self):
  with self.assertRaises(DomainError):await self.adapter(Session(self.payload(request_id='other'))).execute('a'*64,'olt.optical.power',{},'fixture')
 async def test_body_cap_enforced(self):
  with self.assertRaises(DomainError):await self.adapter(Session(b'x'*600000)).execute('a'*64,'olt.optical.power',{},'fixture')
 async def test_write_skills_never_enabled(self):
  with self.assertRaises(DomainError):ReadConnector('https://connector.example','a'*64,['olt.firmware.upgrade'],'s'*64,None)
 async def test_public_insecure_endpoint_refused(self):
  with self.assertRaises(DomainError):ReadConnector('http://remote.example','a'*64,['olt.optical.power'],'s'*64,None)
 async def test_url_or_credentials_in_input_refused(self):
  s=Session(self.payload())
  with self.assertRaises(DomainError):await self.adapter(s).execute('a'*64,'olt.optical.power',{'url':'http://bad'},'fixture')
  self.assertFalse(s.calls)
