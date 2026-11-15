"""Private independent Atlas acceptance, synthetic inputs only."""
import copy,hashlib,importlib.util,json,os,sqlite3,sys,tempfile,unittest,concurrent.futures
from pathlib import Path
PROJECT=Path(os.environ.get('HARMONIKA_EXAM_PROJECT','/app'));sys.path.insert(0,str(PROJECT))
from workspace import WorkspaceStore
class Atlas(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite';self.q=WorkspaceStore(self.path)
 def tearDown(self):self.tmp.cleanup()
 def job(self,owner='alpha',project='one',key='one',units=4):
  s,v=self.q.submit(owner,project,key,{'units':units});self.assertEqual(s,202,v);return v['job']['id']
 def finish(self,id,owner='alpha',project='one'):
  for _ in range(100):
   if self.q.get(owner,project,id)[1]['job']['state']=='completed':return
   self.q.tick()
  self.fail('unfinished')
 def test_01_wal_reload(self):
  id=self.job();q=WorkspaceStore(self.path);self.assertEqual(q.get('alpha','one',id)[0],200)
  with sqlite3.connect(self.path) as c:self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'wal');self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
 def test_02_four_scope_identities(self):
  ids=[self.job(o,p) for o,p in [('alpha','one'),('alpha','two'),('beta','one'),('beta','two')]];self.assertEqual(len(set(ids)),4)
 def test_03_foreign_get_lookup(self):
  id=self.job()
  for o,p in [('alpha','two'),('beta','one')]:self.assertEqual(self.q.get(o,p,id)[0],404);self.assertEqual(self.q.lookup(o,p,'one')[0],404)
 def test_04_bad_identity(self):
  for o,p in [('other','one'),('alpha','../one'),(None,'one')]:self.assertEqual(self.q.submit(o,p,'a',{'units':1})[0],404)
 def test_05_job_validation(self):
  for body in [None,[],{}, {'units':True},{'units':0},{'units':51},{'units':'1'},{'units':1,'owner':'beta'}]:self.assertEqual(self.q.submit('alpha','one','a',body)[0],422)
  for key in ['', '../x','é','a'*65,None]:self.assertEqual(self.q.submit('alpha','one',key,{'units':1})[0],422)
  self.assertEqual(self.q.mechanics()['jobs'],0)
 def test_06_job_replay_conflict(self):
  id=self.job();s,v=self.q.submit('alpha','one','one',{'units':4});self.assertEqual(s,200);self.assertEqual(v['job']['id'],id);self.assertIs(v['replayed'],True);self.assertEqual(self.q.submit('alpha','one','one',{'units':3})[0],409)
 def test_07_scope_quota_replay(self):
  id=self.job();self.job(key='two');self.assertEqual(self.q.submit('alpha','one','excess',{'units':1})[0],429);self.assertEqual(self.q.submit('alpha','one','one',{'units':4})[0],200);self.q.cancel('alpha','one',id);self.assertEqual(self.q.submit('alpha','one','excess',{'units':1})[0],202)
 def test_08_global_quota(self):
  self.job(key='a');self.job(key='b');self.job(project='two',key='a');self.job(owner='beta',key='a');self.assertEqual(self.q.submit('beta','two','fifth',{'units':1})[0],429)
 def test_09_atomic_parallel_idempotency(self):
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:rows=list(pool.map(lambda _:WorkspaceStore(self.path).submit('alpha','one','parallel',{'units':3}),range(16)))
  accepted=[v['job']['id'] for s,v in rows if s in [200,202]];self.assertTrue(accepted);self.assertEqual(len(set(accepted)),1);self.assertEqual(self.q.mechanics()['jobs'],1)
 def test_10_scope_round_robin_persists(self):
  ids=[self.job(o,p,'x',10) for o,p in [('alpha','one'),('alpha','two'),('beta','one')]]
  for _ in range(9):WorkspaceStore(self.path).tick()
  self.assertEqual([self.q.get(o,p,id)[1]['job']['done'] for (o,p),id in zip([('alpha','one'),('alpha','two'),('beta','one')],ids)],[3,3,3])
 def test_11_fifo_within_scope(self):
  a=self.job(key='a',units=3);b=self.job(key='b',units=3)
  for _ in range(3):self.q.tick()
  self.assertEqual(self.q.get('alpha','one',a)[1]['job']['state'],'completed');self.assertEqual(self.q.get('alpha','one',b)[1]['job']['done'],0)
 def test_12_concurrent_ticks_no_duplicate_units(self):
  id=self.job(units=50)
  with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(lambda _:WorkspaceStore(self.path).tick(),range(90)))
  self.finish(id)
  with sqlite3.connect(self.path) as c:rows=c.execute('SELECT unit FROM units WHERE job_id=? ORDER BY unit',(id,)).fetchall()
  self.assertEqual(rows,[(i,) for i in range(1,51)]);self.assertEqual(self.q.mechanics()['units'],50)
 def test_13_cancellation_stops_units(self):
  id=self.job();self.q.tick();self.assertEqual(self.q.cancel('alpha','one',id)[1]['job']['state'],'cancelled')
  before=self.q.mechanics();self.q.cancel('alpha','one',id)
  for _ in range(5):self.q.tick()
  self.assertEqual(self.q.mechanics(),before);self.assertEqual(self.q.get('alpha','one',id)[1]['job']['done'],1)
 def test_14_foreign_cancel_artifact(self):
  id=self.job()
  for o,p in [('alpha','two'),('beta','one')]:self.assertEqual(self.q.cancel(o,p,id)[0],404);self.assertEqual(self.q.artifact(o,p,id)[0],404)
  self.assertEqual(self.q.get('alpha','one',id)[1]['job']['state'],'queued')
 def test_15_artifact_exact_and_terminal_cancel(self):
  id=self.job(units=2);self.assertEqual(self.q.artifact('alpha','one',id)[0],409);self.finish(id);self.assertEqual(self.q.artifact('alpha','one',id),(200,('project,job_id,units\r\none,'+id+',2\r\n').encode()));self.assertEqual(self.q.cancel('alpha','one',id)[1]['job']['state'],'completed')
 def test_16_event_pagination_scope(self):
  id=self.job();self.job(owner='beta');self.q.tick();s,a=self.q.events('alpha','one',0,1);self.assertEqual(s,200);self.assertEqual(len(a['events']),1);s,b=self.q.events('alpha','one',a['cursor'],100);rows=a['events']+b['events'];self.assertTrue(rows);self.assertTrue(all(x['job_id']==id for x in rows));self.assertEqual([x['id'] for x in rows],sorted(set(x['id'] for x in rows)))
 def test_17_bad_cursors(self):
  for after,limit in [(-1,1),(True,1),(0,True),(0,0),(0,101),('1',1)]:self.assertEqual(self.q.events('alpha','one',after,limit)[0],422)
  self.assertEqual(self.q.events('alpha','one',999,1),(200,{'events':[],'cursor':999}))
 def test_18_ticket_scope_and_normalization(self):
  data={'title':'  العربية  ','status':'open'};s,a=self.q.create_ticket('alpha','one','x',data);self.assertEqual(s,201);self.assertEqual(a['ticket']['title'],'العربية');self.assertEqual(data['title'],'  العربية  ');self.assertEqual(self.q.create_ticket('alpha','two','x',data)[0],201);self.assertEqual(self.q.create_ticket('beta','one','x',data)[0],201);self.assertEqual(len(self.q.list_tickets('alpha','one')[1]['tickets']),1)
 def test_19_ticket_replay_after_edit_uses_initial_fingerprint(self):
  data={'title':'Original','status':'open'};id=self.q.create_ticket('alpha','one','x',data)[1]['ticket']['id'];self.q.update_ticket('alpha','one',id,1,{'status':'done'});s,v=self.q.create_ticket('alpha','one','x',data);self.assertEqual(s,200);self.assertEqual(v['ticket']['revision'],2);self.assertEqual(v['ticket']['status'],'done');self.assertEqual(self.q.create_ticket('alpha','one','x',{'title':'Original','status':'done'})[0],409)
 def test_20_ticket_validation(self):
  for data in [{},[],{'title':' ','status':'open'},{'title':'x'*121,'status':'open'},{'title':'x','status':'bad'},{'title':'x','status':'open','owner':'beta'}]:self.assertEqual(self.q.create_ticket('alpha','one','x',data)[0],422)
 def test_21_optimistic_concurrency(self):
  id=self.q.create_ticket('alpha','one','x',{'title':'x','status':'open'})[1]['ticket']['id']
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:WorkspaceStore(self.path).update_ticket('alpha','one',id,1,{'status':'done'}),range(2)))
  self.assertEqual(sorted(x[0] for x in rows),[200,409]);self.assertEqual(self.q.list_tickets('alpha','one')[1]['tickets'][0]['revision'],2)
 def test_22_foreign_ticket_update(self):
  id=self.q.create_ticket('alpha','one','x',{'title':'x','status':'open'})[1]['ticket']['id'];self.assertEqual(self.q.update_ticket('beta','one',id,1,{'status':'done'})[0],404);self.assertEqual(self.q.update_ticket('alpha','two',id,1,{'status':'done'})[0],404)
 def test_23_snapshot_independence_and_mechanics(self):
  id=self.job();v=self.q.get('alpha','one',id)[1];v['job']['done']=999;self.assertEqual(self.q.get('alpha','one',id)[1]['job']['done'],0);m=self.q.mechanics();self.assertEqual(set(m),{'jobs','units','events'});self.assertTrue(all(type(x) is int for x in m.values()))
 def test_24_real_writer_lock_returns_safe_error(self):
  with sqlite3.connect(self.path) as c:
   c.execute('BEGIN IMMEDIATE');s,v=self.q.submit('alpha','one','locked',{'units':1});self.assertEqual((s,v),(503,{'error':'database_busy'}))
  self.assertEqual(self.q.lookup('alpha','one','locked')[0],404);self.assertEqual(self.q.submit('alpha','one','locked',{'units':1})[0],202)

class Parser(unittest.TestCase):
 def setUp(self):
  spec=importlib.util.spec_from_file_location('tui_subject',PROJECT/'tools/tui.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);self.cls=m.SSEParser;self.scope='a'*16
 def frame(self,id=1,text='日本',event='job',scope=None):return ('id: '+(scope or self.scope)+':'+str(id)+'\nevent: '+event+'\ndata: '+json.dumps({'id':id,'text':text},ensure_ascii=False)+'\n\n').encode()
 def test_25_utf8_crlf_every_split(self):
  raw=self.frame().replace(b'\n',b'\r\n')
  for cut in range(len(raw)+1):
   p=self.cls(self.scope);rows=p.feed(raw[:cut])+p.feed(raw[cut:])+p.finish();self.assertEqual(rows,[{'id':1,'text':'日本'}]);self.assertEqual(p.cursor,self.scope+':1')
 def test_26_partial_never_advances(self):
  p=self.cls(self.scope);self.assertEqual(p.feed(self.frame()[:-1]),[]);self.assertIsNone(p.cursor)
  with self.assertRaises(ValueError):p.finish()
 def test_27_invalid_scope_and_json(self):
  for raw in [self.frame(scope='b'*16),b'id: '+self.scope.encode()+b':1\ndata: garbage\n\n',b'data: \xff\n\n']:
   p=self.cls(self.scope)
   with self.assertRaises(ValueError):p.feed(raw)
   self.assertIsNone(p.cursor)
 def test_28_cursor_id_must_match(self):
  p=self.cls(self.scope)
  with self.assertRaises(ValueError):p.feed(self.frame().replace(b'"id": 1',b'"id": 2'))
  self.assertIsNone(p.cursor)
 def test_29_default_message_and_unknown(self):
  for raw in [self.frame(event='message'),self.frame().replace(b'event: job\n',b'')]:
   p=self.cls(self.scope);self.assertEqual(len(p.feed(raw)),1)
  p=self.cls(self.scope);self.assertEqual(p.feed(b': comment\n\n'+self.frame(event='future')),[]);self.assertIsNone(p.cursor)
 def test_30_unframed_cap_and_nonbytes(self):
  for raw in [b'x'*65537,'not bytes']:
   with self.assertRaises(ValueError):self.cls(self.scope).feed(raw)
if __name__=='__main__':unittest.main(verbosity=2)
