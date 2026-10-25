import asyncio,json,tempfile,unittest
from pathlib import Path
from backend.unified_engine.priority import PrioritySlots
from backend.unified_engine.realtime import Tickets
from backend.unified_engine.graphql_api import make_schema,run
from backend.unified_engine.state import State,Error

class Priorities(unittest.IsolatedAsyncioTestCase):
 async def test_priority_fifo_and_no_preemption(self):
  slots=PrioritySlots(1);order=[];release=asyncio.Event()
  async def task(name,priority,block=False):
   async with slots.slot(priority):
    order.append(name)
    if block:await release.wait()
  first=asyncio.create_task(task('active',3,True));await asyncio.sleep(0)
  low=asyncio.create_task(task('low',3));high=asyncio.create_task(task('high',1));second=asyncio.create_task(task('high2',1));await asyncio.sleep(0);release.set();await asyncio.gather(first,low,high,second)
  self.assertEqual(order,['active','high','high2','low']);self.assertEqual(slots.active,0)
 async def test_cancel_waiting_and_granted_does_not_leak(self):
  slots=PrioritySlots(1)
  async with slots.slot():
   async def pending():
    async with slots.slot():pass
   p=asyncio.create_task(pending());await asyncio.sleep(0);p.cancel();await asyncio.gather(p,return_exceptions=True)
  self.assertEqual(slots.active,0)
  async with slots.slot():self.assertEqual(slots.active,1)

class TicketChecks(unittest.TestCase):
 def test_single_use_origin_expiry_and_capacity(self):
  t=Tickets();v=t.issue('api-token:1','Bearer hai_synthetic',{'chat'},'https://example.test');record=t.consume(v['ticket'],'https://example.test');self.assertEqual(record['actor'],'api-token:1')
  with self.assertRaises(Error):t.consume(v['ticket'],'https://example.test')
  v=t.issue('api-token:1','Bearer hai_synthetic',{'chat'},'https://example.test')
  with self.assertRaisesRegex(Error,'ticket_origin_mismatch'):t.consume(v['ticket'],'https://evil.test')
  v=t.issue('api-token:1','Bearer hai_synthetic',{'chat'},'')
  for record in t.items.values():record['deadline']=0
  with self.assertRaises(Error):t.consume(v['ticket'],'')
 def test_no_unlimited_ticket_allocation(self):
  t=Tickets()
  for _ in range(4):t.issue('api-token:1','Bearer hai_synthetic',{'chat'},'')
  with self.assertRaises(Error):t.issue('api-token:1','Bearer hai_synthetic',{'chat'},'')

class FakeExtensions:
 def query(self,query,limit=5):return {'results':[{'text':'synthetic','source':'https://example.test'}]}
 def replay(self,actor,topic,after=0,limit=100):return {'events':[],'actor_matches':actor=='api-token:1'}
 def secret_metadata(self,actor,name):return {'name':name,'value_returned':False}

class GraphQL(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.state=State(Path(self.temp.name)/'private/state.sqlite',b'x'*32);self.schema=make_schema(self.state,FakeExtensions())
 def test_real_query_variables_fragments_and_provenance(self):
  s=self.state.session('api-token:1');v=run(self.schema,{'query':'query Q($id:String!){ session(id:$id) ...C } fragment C on Query { capabilities }','variables':{'id':s['id']}},'api-token:1',{'chat'});self.assertNotIn('errors',v);self.assertEqual(v['data']['session']['id'],s['id']);self.assertEqual(v['data']['capabilities']['catalog_size'],70)
 def test_other_actor_and_secret_scope_never_leak(self):
  s=self.state.session('api-token:1');v=run(self.schema,{'query':'{session(id:"'+s['id']+'")} '},'api-token:2',{'chat'});self.assertIsNone(v['data']['session']);self.assertEqual(v['errors'][0]['extensions']['code'],'not_found')
  v=run(self.schema,{'query':'{secretMetadata(name:"connector")}'},'api-token:1',{'chat'});self.assertEqual(v['errors'][0]['extensions']['code'],'secret_scope_required')
 def test_mutations_introspection_fragments_and_cost_limits(self):
  for query in ['mutation{deleteEverything}','{__schema{types{name}}}','{...A} fragment A on Query {...A}','{'+''.join('a%d:capabilities '%i for i in range(50))+'}']:
   with self.assertRaises(Error):run(self.schema,{'query':query},'api-token:1',{'chat'})
 def test_error_messages_do_not_echo_input_values(self):
  v=run(self.schema,{'query':'{job(id:"private-secret-value")}'},'api-token:1',{'chat'});self.assertNotIn('private-secret-value',json.dumps(v));self.assertIn('errors',v)

if __name__=='__main__':unittest.main()
