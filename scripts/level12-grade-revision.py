"""Independent additive migration and retained-data checks; no candidate edits."""
import concurrent.futures, hashlib, importlib.util, json, sqlite3, sys, time, unittest, uuid
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('baseline_grader', REPO/'scripts/level12-grade.py')
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)
C, R, P = G.C, G.R, G.P
V1 = G.load('sealed_baseline_crm', P/'reference/v1/crm.py')
ADMIN, A, B = G.ADMIN, G.A, G.B

class Revision(G.Domain):
    def test_schema_compatibility(self):
        c = self.customer()
        self.assertEqual(self.s.customer(A, c['id'])['segment'], 'general')
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
        self.s.update_customer(A, c['id'], 1, {'segment':'vip'})
        old = V1.CRMStore(self.db)
        self.assertNotIn('segment', old.customer(A, c['id']))
        old.update_customer(A, c['id'], 2, {'name':'Common edit from V1'})
        current = C.CRMStore(self.db).customer(A, c['id'])
        self.assertEqual(current['segment'], 'vip')
        self.assertEqual(current['name'], 'Common edit from V1')
        old_row = old.create_customer(A, 'old-code-new-row', {'name':'Old code', 'email':'old@example.invalid'})['customer']
        self.assertEqual(C.CRMStore(self.db).customer(A, old_row['id'])['segment'], 'general')
        with sqlite3.connect(self.db) as db: db.execute('PRAGMA user_version=99')
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.error(503, lambda:C.CRMStore(self.db), 'schema_unsupported')
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def legacy(self):
        path = Path(self.t.name)/'legacy.sqlite'
        old = V1.CRMStore(path)
        payload = {'name':'Legacy person','email':'legacy@example.invalid','stage':'lead'}
        row = old.create_customer(A,'legacy-key',payload)['customer']
        return path, old, payload, row

    def test_migration_preserves_original_request_fingerprint(self):
        path, old, payload, row = self.legacy()
        with sqlite3.connect(path) as db: fingerprint = db.execute('SELECT fingerprint FROM requests').fetchone()[0]
        current = C.CRMStore(path)
        reply = current.create_customer(A,'legacy-key',payload)
        self.assertTrue(reply['replayed']); self.assertEqual(reply['customer']['id'], row['id'])
        self.assertEqual(reply['customer']['segment'], 'general')
        current.update_customer(A,row['id'],1,{'segment':'vip'})
        self.assertEqual(current.create_customer(A,'legacy-key',payload)['customer']['segment'],'vip')
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT fingerprint FROM requests').fetchone()[0],fingerprint)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
        self.error(409,lambda:current.create_customer(A,'legacy-key',{**payload,'segment':'general'}))

    def test_migration_failure_is_atomic(self):
        path, old, payload, row = self.legacy(); triggered = []
        real_connect = sqlite3.connect
        class Injected(sqlite3.Connection):
            def execute(conn, sql, *args, **kw):
                if ''.join(sql.lower().split()).rstrip(';') == 'pragmauser_version=2':
                    triggered.append(True); raise sqlite3.OperationalError('injected migration failure')
                return super().execute(sql,*args,**kw)
        def connect(*args, **kw): return real_connect(*args, **{**kw,'factory':Injected})
        with mock.patch.object(C.sqlite3,'connect',connect):
            with self.assertRaises(C.AppError): C.CRMStore(path)
        self.assertTrue(triggered,'migration version write was not exercised')
        with real_connect(path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertNotIn('segment',[r[1] for r in db.execute('PRAGMA table_info(customers)')])
            self.assertEqual(db.execute('SELECT count(*) FROM customers').fetchone()[0],1)
        current=C.CRMStore(path)
        self.assertEqual(current.create_customer(A,'legacy-key',payload)['customer']['id'],row['id'])

    def test_migration_real_writer_lock_then_same_data(self):
        path, old, payload, row = self.legacy()
        holder=sqlite3.connect(path); holder.execute('BEGIN IMMEDIATE')
        try: self.error(503,lambda:C.CRMStore(path),'sqlite_busy')
        finally: holder.rollback();holder.close()
        self.assertEqual(C.CRMStore(path).create_customer(A,'legacy-key',payload)['customer']['id'],row['id'])

    def test_segment_validation_filters_and_ownership(self):
        first=self.customer();second=self.customer(key='segment-vip',name='VIP',segment='vip')
        self.assertEqual([r['id'] for r in self.s.customers(A,segment='vip')],[second['id']])
        self.assertEqual([r['id'] for r in self.s.customers(A,segment='general')],[first['id']])
        self.assertEqual(self.s.customers(B,segment='vip'),[])
        for value in [True,0,[],{},'VIP','',None]:
            self.error(422,lambda value=value:self.s.update_customer(A,first['id'],1,{'segment':value}))
        for value in [True,0,[],{},'VIP','']:
            self.error(422,lambda value=value:self.s.customers(A,segment=value))

class LiveRevision(G.HTTP):
    def test_segment_real_api(self):
        key='v2-independent-'+uuid.uuid4().hex
        body={'name':'Revision proof','email':'revision@example.invalid','stage':'lead','segment':'vip'}
        first=self.api('POST','/api/customers',body,'staff-a',key=key)
        self.assertEqual(first['status'],201);row=first['body']['customer']
        self.assertEqual(row['segment'],'vip')
        filtered=self.api('GET','/api/customers?segment=vip',role='staff-a')
        self.assertEqual(filtered['status'],200);self.assertIn(row['id'],[r['id'] for r in filtered['body']['customers']])
        self.assertEqual(self.api('GET','/api/customers?segment=BAD',role='staff-a')['status'],422)
        self.assertEqual(self.api('GET','/api/customers/'+row['id'],role='staff-b')['status'],404)
        self.assertEqual(self.api('POST','/api/customers',body,'staff-a',key=key)['status'],200)

if __name__=='__main__':
    state=G.O.remote({'op':'status'});manifest=state['current']
    if not state['pid_bound_ready'] or not all(hashlib.sha256((P/n).read_bytes()).hexdigest()==sha for n,sha in manifest['hashes'].items()):
        raise RuntimeError('deployment_source_mismatch')
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Revision)
    if '--http' in sys.argv:suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(LiveRevision))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    value={'passed':result.wasSuccessful(),'cases':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'release':manifest['release'],'source_sha256':manifest['source_sha256']}
    (R/('revision-grade-'+str(time.time_ns())+'.json')).write_text(json.dumps(value,indent=2))
    sys.exit(not result.wasSuccessful())
