import asyncio,unittest
from unittest.mock import patch
try:
 from aiohttp.test_utils import TestClient,TestServer
 from backend.master_engine.service import make_app
 READY=True
except ModuleNotFoundError as e:
 if not e.name.startswith('aiohttp'):raise
 READY=False
from backend.master_engine.index import descriptors,summary,restricted
@unittest.skipUnless(READY,'dedicated master venv suite required')
class HTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.skills={'static.ast.parse':{'description':'test parser','input_schema':{'type':'object'}}};self.h={'X-Harmonika-Master-Secret':'s'*64,'X-Harmonika-Principal':'api-token:1'}
  def dispatch(id,args):
   if args.get('invalid'):raise ValueError('SENSITIVE_INPUT_DO_NOT_LOG')
   return {'status':'completed','analysis_performed':True,'data':{'node_count':3}}
  self.client=TestClient(TestServer(make_app('s'*64,self.skills,dispatch)));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close()
 async def test_boundary_requires_gateway_principal_and_no_store(self):
  r=await self.client.get('/healthz');self.assertEqual((await r.json())['service'],'harmonika-master-skills')
  for headers in ({},{**self.h,'X-Harmonika-Principal':'root'},{**self.h,'X-Harmonika-Master-Secret':'wrong'}):self.assertEqual((await self.client.get('/capabilities',headers=headers)).status,403)
  r=await self.client.get('/capabilities',headers=self.h);v=await r.json();self.assertEqual(v['source_unique'],1200);self.assertEqual(v['existing_registered'],601);self.assertEqual(r.headers['Cache-Control'],'no-store')
 async def test_no_user_credential_identity_or_engine_injection(self):
  for body in ({'skill_id':'static.ast.parse','input':{},'actor':'api-token:2'},{'skill_id':'static.ast.parse','input':{},'secret':'anything'},{'skill_id':'static.ast.parse','input':{},'context':{'engine':'legal'}}):
   r=await self.client.post('/invoke',headers=self.h,json=body);self.assertEqual(r.status,400)
 async def test_analysis_and_customer_safe_error(self):
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'static.ast.parse','input':{}});self.assertEqual(r.status,200);self.assertTrue((await r.json())['analysis_performed'])
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'static.ast.parse','input':{'invalid':True}});self.assertEqual(r.status,400);self.assertNotIn('SENSITIVE_INPUT',await r.text())
 async def test_restricted_missing_and_context_are_distinct(self):
  for id,status,code in [('exploit.reverse_shell',403,'restricted_operation'),('proxy.mobile.buy',501,'implementation_missing'),('fs.read',409,'engine_context_required'),('totally.unknown',404,'unknown_skill')]:
   r=await self.client.post('/invoke',headers=self.h,json={'skill_id':id,'input':{}});self.assertEqual(r.status,status,id);self.assertEqual((await r.json())['error']['code'],code)
 async def test_pagination_limit_and_mutation_independence(self):
  r=await self.client.get('/skills?limit=201',headers=self.h);self.assertEqual(r.status,400)
  r=await self.client.get('/skills?limit=2',headers=self.h);self.assertEqual(len((await r.json())['skills']),2)
  r=await self.client.get('/skills?url=http://169.254.169.254',headers=self.h);self.assertEqual(r.status,400)
  r=await self.client.get('/skills/static.ast.parse',headers=self.h);self.assertEqual((await r.json())['skill']['status'],'available')
 async def test_every_unavailable_descriptor_fails_without_execution(self):
  for row in descriptors(self.skills):
   status=row['status']
   if status=='available':continue
   expected=403 if status=='restricted' else 409 if status in ('context_required','local_only','domain_ambiguous') else 501
   r=await self.client.post('/invoke',headers=self.h,json={'skill_id':row['id'],'input':{}})
   self.assertEqual(r.status,expected,row['id']);self.assertIn('error',await r.json())
 async def test_deep_json_rejected(self):
  value={}
  for _ in range(25):value={'x':value}
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'static.ast.parse','input':value});self.assertEqual(r.status,400)
class Index(unittest.TestCase):
 def test_exact_primary_count_duplicates_and_no_aliases(self):
  v=summary();self.assertEqual(v['source_unique'],1200);self.assertEqual(v['source_occurrences'],1203);self.assertEqual(v['duplicates'],{'rebuild.apply_diff':4});self.assertNotEqual(v['declared_skills'],v['source_unique']);self.assertFalse(v['features']['autonomous_exploitation'])
  d=next(x for x in descriptors() if x['id']=='clone.deploy.vercel');self.assertEqual(d['status'],'unavailable')
 def test_all_seven_cross_domain_ids_require_confirmation(self):
  for id in ('debug.attach','debug.breakpoint','test.run','test.single','test.coverage','evidence.chain.custody','evidence.store.secure'):
   self.assertEqual(next(x for x in descriptors() if x['id']==id)['status'],'domain_ambiguous')
 def test_deep_copy_descriptors(self):
  rows=descriptors();x=next(x for x in rows if x['id']=='scaffold.nextjs');x['input_schema'].clear();self.assertTrue(next(x for x in descriptors() if x['id']=='scaffold.nextjs')['input_schema'])
