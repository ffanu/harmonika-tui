import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
spec=importlib.util.spec_from_file_location('state',Path(__file__).resolve().parents[1]/'runtime/tools/state_store.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class State(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.native=self.root/'native';self.native.mkdir()
        self.db=self.native/'native.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,message_count INTEGER,updated_at INTEGER)')
            c.execute('CREATE TABLE messages(session_id TEXT,parts TEXT)')
            c.execute('INSERT INTO sessions VALUES(?,?,?,?)',('session-1',None,2,1))
            c.execute('INSERT INTO messages VALUES(?,?)',('session-1','private text never copied'))
        self.cache=self.root/'cache'
    def test_persistence_reopen_and_resume_pointer(self):
        result=m.perform('checkpoint',self.native,self.root,self.cache)
        self.assertEqual(result['session_id'],'session-1')
        resumed=m.perform('resume',self.native,self.root,self.cache)
        self.assertEqual(resumed['argv'][-2:],['-s','session-1'])
        self.assertFalse(resumed['automatic_attach'])
        self.assertEqual(m.Cache(self.cache).get(self.root)['session_id'],'session-1')
    def test_native_unchanged_no_body_or_key_in_cache(self):
        before=self.db.read_bytes();result=m.perform('status',self.native,self.root,self.cache)
        self.assertTrue(result['ok']);m.perform('checkpoint',self.native,self.root,self.cache)
        self.assertEqual(before,self.db.read_bytes())
        self.assertNotIn(b'private text', (self.cache/'session-cache.sqlite3').read_bytes())
        self.assertNotIn('parts',json.dumps(result))
    def test_cache_clear_keeps_native_history(self):
        m.perform('checkpoint',self.native,self.root,self.cache)
        m.perform('clear-cache',self.native,self.root,self.cache)
        self.assertIsNone(m.Cache(self.cache).get(self.root));self.assertEqual(m.native_sessions(self.native)['sessions'][0]['message_count'],2)
    def test_project_isolation(self):
        cache=m.Cache(self.cache);cache.put(self.root/'a','a');cache.put(self.root/'b','b')
        self.assertEqual(cache.get(self.root/'a')['session_id'],'a');self.assertIsNone(cache.get(self.root/'c'))
    def test_ttl_and_bound(self):
        now=[100000];cache=m.Cache(self.cache,clock=lambda:now[0]);cache.put(self.root,'old')
        now[0]+=m.TTL+1;self.assertIsNone(cache.get(self.root))
        for i in range(m.MAX_ENTRIES+2):cache.put(self.root/str(i),'s'+str(i));now[0]+=1
        with sqlite3.connect(cache.path) as c:self.assertEqual(c.execute('SELECT count(*) FROM checkpoints').fetchone()[0],m.MAX_ENTRIES)
    def test_private_permissions_and_symlink_rejected(self):
        cache=m.Cache(self.cache)
        self.assertEqual(stat.S_IMODE(cache.path.stat().st_mode),0o600);self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode),0o700)
        link=self.root/'link';link.symlink_to(self.cache,target_is_directory=True)
        with self.assertRaises(m.StateError):m.Cache(link)
    def test_corruption_preserved_and_reported(self):
        self.cache.mkdir();p=self.cache/'session-cache.sqlite3';p.write_bytes(b'broken')
        with self.assertRaisesRegex(m.StateError,'cache_unavailable'):m.Cache(self.cache)
        self.assertEqual(p.read_bytes(),b'broken')
    def test_stale_checkpoint_not_resumed(self):
        m.perform('checkpoint',self.native,self.root,self.cache)
        with sqlite3.connect(self.db) as c:c.execute('DELETE FROM sessions')
        with self.assertRaisesRegex(m.StateError,'session_not_found'):m.perform('resume',self.native,self.root,self.cache)
    def test_locked_database_degrades_no_stale_fallback(self):
        with sqlite3.connect(self.db) as c:
            c.execute('BEGIN EXCLUSIVE')
            result=m.inspect(self.native)
            self.assertEqual(result['status'],'degraded');self.assertEqual(result['sessions'],[])
    def test_active_wal_visible(self):
        with sqlite3.connect(self.db) as c:
            c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA wal_autocheckpoint=0')
            c.execute('INSERT INTO sessions VALUES(?,?,?,?)',('session-wal',None,3,2));c.commit()
            self.assertEqual(m.native_sessions(self.native)['sessions'][0]['session_id'],'session-wal')
    def test_concurrent_writers(self):
        cache=m.Cache(self.cache)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda i:cache.put(self.root/str(i),'s'+str(i)),range(20)))
        for i in range(20):self.assertEqual(cache.get(self.root/str(i))['session_id'],'s'+str(i))
    def test_invalid_schema_and_id_degrade(self):
        with sqlite3.connect(self.db) as c:c.execute("UPDATE sessions SET id='../unsafe'")
        self.assertEqual(m.inspect(self.native)['error'],'invalid_native_session')
    def test_status_does_not_create_cache(self):
        result=m.perform('status',self.native,self.root,self.cache)
        self.assertTrue(result['ok']);self.assertEqual(result['cache']['status'],'empty');self.assertFalse(self.cache.exists())
    def test_overview_recent_five_and_expired_no_paths_or_ids(self):
        now=100000;cache=m.Cache(self.cache,clock=lambda:now)
        for i in range(7):cache.put(self.root/str(i),'secret-session-'+str(i))
        with sqlite3.connect(cache.path) as c:c.execute('UPDATE checkpoints SET updated_at=0 WHERE project_hash=?',(cache.key(self.root/'0'),))
        before=cache.path.read_bytes();overview=m.cache_overview(self.cache,clock=lambda:now)
        self.assertEqual(overview['entries'],6);self.assertEqual(overview['expired_entries'],1);self.assertEqual(len(overview['recent_checkpoints']),5)
        self.assertEqual(before,cache.path.read_bytes());text=json.dumps(overview)
        self.assertNotIn('secret-session',text);self.assertNotIn(str(self.root),text)
    def test_corrupt_cache_degrades_status_without_reset(self):
        self.cache.mkdir();p=self.cache/'session-cache.sqlite3';p.write_bytes(b'broken');p.chmod(0o600)
        result=m.perform('status',self.native,self.root,self.cache)
        self.assertFalse(result['ok']);self.assertEqual(result['cache']['status'],'degraded');self.assertEqual(p.read_bytes(),b'broken')
    def test_storage_counts_wal_and_ignores_symlink(self):
        wal=Path(str(self.db)+'-wal');wal.write_bytes(b'x'*123)
        shm=Path(str(self.db)+'-shm');shm.symlink_to(self.db)
        sizes=m.storage_size(self.db);self.assertEqual(sizes['wal_bytes'],123);self.assertEqual(sizes['shm_bytes'],0)
        self.assertEqual(sizes['total_bytes'],self.db.stat().st_size+123)
    def test_text_renderer_bounds_bar_width_and_zero_size(self):
        for value in [0,1024,1024**5]:
            result={'ok':True,'native':{'status':'ok','integrity':'ok','storage':{'database_bytes':value,'total_bytes':value}},'cache':{'status':'empty'}}
            text=m.render_status(result);self.assertNotIn('\x1b',text)
            for line in text.splitlines():
                if '[' in line:self.assertEqual(len(line.split('[')[1].split(']')[0]),20)
    def test_cli_json_default_and_text(self):
        import subprocess
        env=dict(os.environ,HARMONIKA_CONFIG_DIR=str(self.root/'config'))
        command=[os.sys.executable,str(Path(m.__file__)),'status','--data-dir',str(self.native)]
        for args in [[],['--format','json'],['--format','text']]:
            result=subprocess.run(command+args,capture_output=True,text=True,env=env,timeout=5)
            self.assertEqual(result.returncode,0)
            if 'text' in args:self.assertIn('Ukuran total:',result.stdout)
            else:self.assertTrue(json.loads(result.stdout)['ok'])
