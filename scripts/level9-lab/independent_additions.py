class HighAvailability(Base):
 def store(self,**kw):
  kw.setdefault('schema_version',3);kw.setdefault('coordinator_id','c'+str(len(self.stores)+1));return super().store(**kw)
 def test_23_schema3_additive_failure_and_legacy_checkpoint(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'wf.sqlite';old=WorkflowStore(path,self.child,schema_version=2);status,v=old.submit('alpha','old',request());wid=v['workflow']['id'];self.pump(wid,q=old);artifact=old.artifact('alpha',wid)
   with sqlite3.connect(path) as c:before=c.iterdump();before='\n'.join(before)
   def fail(stage):
    if stage=='after_claim_owner':raise RuntimeError('PRIVATE_ERROR_CANARY')
   with self.assertRaises(RuntimeError):WorkflowStore(path,self.child,schema_version=3,migration_hook=fail)
   with sqlite3.connect(path) as c:self.assertEqual('\n'.join(c.iterdump()),before);self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],2)
   new=WorkflowStore(path,self.child,schema_version=3);self.assertEqual(new.artifact('alpha',wid),artifact)
   with sqlite3.connect(path) as c:
    self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],3);self.assertEqual(c.execute('SELECT claim_owner,claim_epoch,lease_until FROM steps LIMIT 1').fetchone(),('',0,0.0));self.assertEqual(c.execute('SELECT count(*) FROM coordinator_audit').fetchone()[0],0)
   old.stop();new.stop()
 def test_24_fresh_schema3_failure_rolls_back_everything(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'fresh.sqlite'
   def fail(stage):
    if stage=='after_claim_owner':raise RuntimeError('fail')
   with self.assertRaises(RuntimeError):WorkflowStore(path,self.child,schema_version=3,migration_hook=fail)
   with sqlite3.connect(path) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],0);self.assertEqual(c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[])
 def test_25_stale_live_coordinator_cannot_settle_after_takeover(self):
  entered=threading.Event();release=threading.Event();clock=[1000.0];original=self.child.submit;first=[True]
  def held(*args):
   result=original(*args)
   if first[0]:first[0]=False;entered.set();release.wait(3)
   return result
  self.child.submit=held;a=self.store(coordinator_id='older',lease_seconds=.1,clock=lambda:clock[0]);b=self.store(coordinator_id='newer',lease_seconds=.1,clock=lambda:clock[0]);status,v=a.submit('alpha','stale',request());wid=v['workflow']['id'];errors=[]
  def tick():
   try:a.tick()
   except Exception as e:errors.append(type(e).__name__)
  t=threading.Thread(target=tick);t.start()
  try:
   self.assertTrue(entered.wait(2));b.tick();self.assertEqual(sum(x[0]=='submit' for x in self.child.calls),1);clock[0]+=1;b.tick();clock[0]+=1;b.tick();before=b.artifact('alpha',wid);self.assertEqual(before[0],200)
  finally:release.set();t.join(4)
  self.assertFalse(t.is_alive());self.assertFalse(errors);self.assertEqual(b.artifact('alpha',wid),before);self.assertEqual(sum(x[0]=='submit' for x in self.child.calls),2)
  rows=self.rows('coordinator_audit');self.assertTrue(any(x['event']=='stale_rejected' and x['coordinator_id']=='older' for x in rows));root=[x for x in self.rows('steps') if x['workflow_id']==wid and x['name']=='prepare'][0];self.assertGreaterEqual(root['claim_epoch'],2)
 def test_26_epoch_is_integer_and_increases_on_reacquisition(self):
  clock=[1000.0];a=self.store(coordinator_id='first',lease_seconds=.1,clock=lambda:clock[0]);b=self.store(coordinator_id='second',lease_seconds=.1,clock=lambda:clock[0]);self.child.offline=True;_,v=a.submit('alpha','epochs',request());wid=v['workflow']['id'];a.tick()
  with sqlite3.connect(self.path) as c:c.execute('UPDATE steps SET retry_at=0,lease_until=0 WHERE workflow_id=?',(wid,))
  b.tick();rows=[x for x in self.rows('coordinator_audit') if x['event']=='claim' and x['workflow_id']==wid];self.assertGreaterEqual(len(rows),2);epochs=[x['epoch'] for x in rows];self.assertTrue(all(type(x) is int and x>0 for x in epochs));self.assertEqual(epochs,sorted(set(epochs)))
 def test_27_schema3_global_quota_across_coordinators(self):
  stores=[self.store() for _ in range(12)];barrier=threading.Barrier(len(stores))
  def submit(i):barrier.wait();return stores[i].submit('alpha','ha-'+str(i),request())[0]
  with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:states=list(pool.map(submit,range(12)))
  self.assertEqual(states.count(202),4);self.assertTrue(set(states)<={202,429,503});self.assertEqual(len(self.rows('workflows')),4)
 def test_28_pause_and_cancel_during_reserved_claim(self):
  clock=[1000.0];a=self.store(coordinator_id='first',clock=lambda:clock[0]);b=self.store(coordinator_id='second',clock=lambda:clock[0]);self.child.pending=True;_,v=a.submit('alpha','pause-cancel',request());wid=v['workflow']['id'];a.tick();a.pause('alpha',wid)
  for j in self.child.jobs.values():j['state']='completed'
  clock[0]+=4;b.tick();self.assertEqual(sum(x[0]=='submit' for x in self.child.calls),1);self.assertEqual(b.get('alpha',wid)[1]['workflow']['tasks'][1]['child_id'],None);a.cancel('alpha',wid);clock[0]+=4;b.tick();self.assertEqual(b.get('alpha',wid)[1]['workflow']['state'],'cancelled');self.assertEqual(b.artifact('alpha',wid)[0],409)
 def test_29_schema3_no_write_lock_during_network_io(self):
  entered=threading.Event();release=threading.Event();original=self.child.submit
  def held(*args):entered.set();release.wait(2);return original(*args)
  self.child.submit=held;self.submit();t=threading.Thread(target=self.q.tick);t.start()
  try:
   self.assertTrue(entered.wait(1));c=sqlite3.connect(self.path,timeout=.2);c.execute('BEGIN IMMEDIATE');c.rollback();c.close()
  finally:release.set();t.join(3)
  self.assertFalse(t.is_alive())
 def test_30_audit_mechanics_only_and_snapshot_hides_claim_internals(self):
  wid=self.submit(data=request('PRIVATE_DATA_CANARY'));self.pump(wid);rows=self.rows('coordinator_audit');self.assertTrue(rows);self.assertTrue(all(set(x)=={'id','coordinator_id','workflow_id','task','epoch','event'} for x in rows));self.assertTrue(all(x['event'] in ['claim','settle','stale_rejected'] for x in rows));self.assertNotIn('PRIVATE_DATA_CANARY',json.dumps(rows));snapshot=self.q.get('alpha',wid)[1]['workflow'];self.assertNotIn('claim_owner',json.dumps(snapshot));self.assertNotIn('lease_until',json.dumps(snapshot))
 def test_31_invalid_identity_and_lease_rejected(self):
  for kw in [{'coordinator_id':'bad id'},{'coordinator_id':'../../secret'},{'lease_seconds':True},{'lease_seconds':0},{'lease_seconds':float('inf')},{'lease_seconds':float('nan')}]:
   with self.subTest(keys=list(kw)),self.assertRaises(ValueError):self.store(**kw)
 def test_32_repeated_schema3_init_and_writer_lock(self):
  with sqlite3.connect(self.path) as c:before='\n'.join(c.iterdump())
  def fail(stage):raise RuntimeError('hook_should_not_repeat')
  q=self.store(migration_hook=fail)
  with sqlite3.connect(self.path) as c:self.assertEqual('\n'.join(c.iterdump()),before)
  c=sqlite3.connect(self.path);c.execute('BEGIN IMMEDIATE');began=time.monotonic()
  try:
   with self.assertRaises(sqlite3.OperationalError):self.store()
  finally:c.rollback();c.close()
  self.assertLess(time.monotonic()-began,.8);self.assertEqual(q.submit('alpha','after-lock',request())[0],202)
