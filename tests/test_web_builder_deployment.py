import asyncio,base64,os,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.web_builder.deployment import config,payload,submit
from backend.web_builder.common import BuilderError
class Deployment(unittest.IsolatedAsyncioTestCase):
 def target(self):return {'provider':'vercel','project':'synthetic-app','principals':['api-token:1'],'cost_authorized':True,'production_authorized':False}
 def test_missing_credentials_are_not_treated_as_deployed(self):
  with self.assertRaises(BuilderError):config(None,'api-token:1','prod','preview')
 def test_ownership_cost_and_production_guard(self):
  t=self.target();c=SimpleNamespace(deployments={'demo':t})
  with self.assertRaises(BuilderError):config(c,'api-token:2','demo','preview')
  with self.assertRaises(BuilderError):config(c,'api-token:1','demo','production')
  t['cost_authorized']=False
  with self.assertRaises(BuilderError):config(c,'api-token:1','demo','preview')
 def test_source_payload_has_only_reviewed_environment_and_no_secrets(self):
  t=self.target();fs={'package.json':base64.b64encode(b'{}').decode()};v=payload(t,fs,'fixture','preview');self.assertNotIn('target',v);self.assertEqual(v['files'][0]['encoding'],'base64')
  for path in ('.env','credentials.key','private.pem'):
   with self.assertRaises(BuilderError):payload(t,{path:base64.b64encode(b'fixture').decode()},'fixture','preview')
  with self.assertRaises(BuilderError):payload(t,{'x.js':base64.b64encode(('hai_live_'+'a'*64).encode()).decode()},'fixture','preview')
 async def test_submission_does_not_falsely_claim_remote_build_ready(self):
  rt=SimpleNamespace(connectors=SimpleNamespace(deployments={'demo':self.target()}))
  with patch('backend.web_builder.deployment.request',return_value={'id':'dpl_SYNTHETIC','readyState':'QUEUED'}):v=await submit(rt,{'target':'demo'},{'package.json':'e30='},'fixture','api-token:1')
  self.assertEqual(v['execution_kind'],'deployment_submission');self.assertFalse(v['data']['ready_confirmed']);self.assertTrue(v['data']['remote_submission_started'])
