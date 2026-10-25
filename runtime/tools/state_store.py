"""Native session metadata + bounded private cache. Never stores chat bodies or keys."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

class StateError(ValueError):pass
ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
MAX_ENTRIES=128
TTL=86400

def private_file(path):
    if path.is_symlink():raise StateError('unsafe_cache_path')
    if path.exists():
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:raise StateError('unsafe_cache_file')
        path.chmod(0o600)
    else:
        fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)

def cache_root(root):
    root=Path(root).absolute()
    # Do not follow an arbitrary parent symlink into a different directory.
    if any(p.is_symlink() for p in [root,*root.parents]):raise StateError('unsafe_cache_path')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.stat().st_uid!=os.getuid():raise StateError('unsafe_cache_owner')
    root.chmod(0o700)
    return root

class Cache:
    def __init__(self,root,clock=time.time):
        self.root=cache_root(root);self.path=self.root/'session-cache.sqlite3';self.clock=clock
        private_file(self.path)
        try:
            with closing(sqlite3.connect(str(self.path),timeout=.5)) as c:
                version=c.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0,1):raise StateError('unsupported_cache_version')
                c.execute('PRAGMA journal_mode=DELETE')
                with c:
                    c.execute('CREATE TABLE IF NOT EXISTS checkpoints(project_hash TEXT PRIMARY KEY, session_id TEXT NOT NULL, updated_at INTEGER NOT NULL)')
                    c.execute('PRAGMA user_version=1')
        except sqlite3.Error:raise StateError('cache_unavailable') from None
    @staticmethod
    def key(project):return hashlib.sha256(str(Path(project).resolve()).encode()).hexdigest()
    def put(self,project,session_id):
        if not isinstance(session_id,str) or not ID.fullmatch(session_id):raise StateError('invalid_session_id')
        now=int(self.clock())
        try:
            with closing(sqlite3.connect(str(self.path),timeout=.5)) as c, c:
                c.execute('DELETE FROM checkpoints WHERE updated_at < ?', (now-TTL,))
                c.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?)',(self.key(project),session_id,now))
                c.execute('DELETE FROM checkpoints WHERE project_hash NOT IN (SELECT project_hash FROM checkpoints ORDER BY updated_at DESC,project_hash LIMIT ?)',(MAX_ENTRIES,))
        except sqlite3.Error:raise StateError('cache_unavailable') from None
    def get(self,project):
        try:
            with closing(sqlite3.connect(str(self.path),timeout=.5)) as c:
                row=c.execute('SELECT session_id,updated_at FROM checkpoints WHERE project_hash=?',(self.key(project),)).fetchone()
            if not row or row[1]<int(self.clock())-TTL:return None
            if not ID.fullmatch(row[0]):raise StateError('invalid_cached_session')
            return {'session_id':row[0],'updated_at':row[1]}
        except sqlite3.Error:raise StateError('cache_unavailable') from None
    def clear(self):
        try:
            with closing(sqlite3.connect(str(self.path),timeout=.5)) as c,c:c.execute('DELETE FROM checkpoints')
        except sqlite3.Error:raise StateError('cache_unavailable') from None



def cache_overview(root,clock=time.time):
    root=Path(root).absolute();file=root/'session-cache.sqlite3'
    if any(p.is_symlink() for p in [root,*root.parents]) or file.is_symlink():
        return {'status':'degraded','error':'unsafe_cache_path'}
    if not file.exists():return {'status':'empty','entries':0,'expired_entries':0,'recent_checkpoints':[],'size_bytes':0}
    try:
        info=file.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:raise StateError('unsafe_cache_file')
        if info.st_mode & 0o077:raise StateError('unsafe_cache_permissions')
        with closing(sqlite3.connect(file.as_uri()+'?mode=ro',uri=True,timeout=.5)) as c:
            c.execute('PRAGMA query_only=ON');deadline=time.monotonic()+1
            c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            if c.execute('PRAGMA user_version').fetchone()[0]!=1:raise StateError('unsupported_cache_version')
            cutoff=int(clock())-TTL
            total,expired=c.execute('SELECT count(*),coalesce(sum(updated_at < ?),0) FROM checkpoints',(cutoff,)).fetchone()
            rows=c.execute('SELECT project_hash,updated_at FROM checkpoints WHERE updated_at>=? ORDER BY updated_at DESC,project_hash LIMIT 5',(cutoff,)).fetchall()
        recent=[]
        for key,updated in rows:
            if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key) or not isinstance(updated,int):raise StateError('invalid_cache_metadata')
            recent.append({'project_ref':key[:8],'updated_at':updated})
        return {'status':'ok','entries':total-expired,'expired_entries':expired,'recent_checkpoints':recent,'size_bytes':info.st_size}
    except (sqlite3.Error,OSError,StateError):return {'status':'degraded','error':'cache_unavailable'}


def storage_size(file):
    sizes={}
    for key,suffix in [('database_bytes',''),('wal_bytes','-wal'),('shm_bytes','-shm')]:
        item=Path(str(file)+suffix)
        sizes[key]=item.stat().st_size if item.is_file() and not item.is_symlink() else 0
    sizes['total_bytes']=sum(sizes.values())
    return sizes


def human_bytes(value):
    for unit in ['B','KiB','MiB','GiB','TiB']:
        if value<1024 or unit=='TiB':return f'{value:.1f} {unit}'
        value/=1024


def engine_discovery_status():
    """Read-only gateway metadata. Never touches native sessions or prints keys."""
    key=Path.home()/'.config/harmonika/harmonika-api-key'
    if not key.exists():return {'status':'not_configured'}
    try:
        import importlib.util
        spec=importlib.util.spec_from_file_location('harmonika_state_master_client',Path(__file__).with_name('master_cli.py'))
        client=importlib.util.module_from_spec(spec);spec.loader.exec_module(client)
        value=client.request('GET','/discovery',timeout=2)
        if type(value) is not dict or value.get('error'):return {'status':'unavailable'}
        # Do not surface module paths, source, user payload or free-form errors.
        out={'status':value.get('status') if value.get('status') in ('ready','degraded','disabled','scanning','recovering') else 'unknown'}
        for k in ('registered','rejected','generation','last_scan_at'):
            if type(value.get(k)) in (int,float):out[k]=value[k]
        return out
    except Exception:return {'status':'unavailable'}


def render_status(result):
    native=result.get('native',{});cache=result.get('cache',{})
    lines=['Harmonika TUI | Sesi & cache',
        'Native: '+native.get('status','unknown')+' | Integritas: '+native.get('integrity','not_verified')]
    storage=native.get('storage',{});total=storage.get('total_bytes',0)
    if storage:
        lines.append('Ukuran total: '+human_bytes(total))
        for label,key in [('Database','database_bytes'),('WAL','wal_bytes'),('SHM','shm_bytes')]:
            amount=storage.get(key,0);width=round(amount/total*20) if total else 0
            lines.append(f'{label:8} [{"#"*width+"."*(20-width)}] {human_bytes(amount)}')
        lines.append('Batang = proporsi ukuran file, bukan kapasitas disk.')
    lines.append('Cache: '+cache.get('status','unknown')+f' | Berlaku: {cache.get("entries",0)} | Kedaluwarsa: {cache.get("expired_entries",0)}')
    rows=cache.get('recent_checkpoints',[])
    if rows:
        lines.append('Checkpoint terbaru (referensi proyek tersamarkan):')
        for row in rows:lines.append('  '+row['project_ref']+' | epoch '+str(row['updated_at']))
    else:lines.append('Belum ada checkpoint yang berlaku.')
    if 'engine_discovery' in result:
        d=result['engine_discovery'];lines.append('Engine discovery: '+d.get('status','unknown')+' | Aktif: '+str(d.get('registered',0))+' | Ditolak: '+str(d.get('rejected',0)))
    if not result.get('ok'):lines.append('Pemeriksaan degraded; data lama tidak dipakai sebagai hasil valid.')
    return '\n'.join(lines)


def native_sessions(directory,limit=20):
    directory=Path(directory).expanduser().resolve()
    if not 1<=limit<=100:raise StateError('invalid_limit')
    if not directory.is_dir():return {'status':'empty','sessions':[],'integrity':'not_checked'}
    candidates=[]
    for p in directory.glob('*.db'):
        if not p.is_file() or p.is_symlink():continue
        try:
            with p.open('rb') as stream:header=stream.read(16)
            if header==b'SQLite format 3\0':candidates.append(p)
        except OSError:raise StateError('native_store_unavailable') from None
    if not candidates:return {'status':'empty','sessions':[],'integrity':'not_checked'}
    if len(candidates)!=1:raise StateError('ambiguous_native_store')
    # No immutable=1 fallback: it may silently omit active WAL transactions.
    try:
        with closing(sqlite3.connect(candidates[0].as_uri()+'?mode=ro',uri=True,timeout=.5)) as c:
            c.execute('PRAGMA query_only=ON');c.execute('PRAGMA busy_timeout=500')
            # Bound inspection; never read messages.parts, titles, prompts or credentials.
            deadline=time.monotonic()+1
            c.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
            columns={row[1] for row in c.execute('PRAGMA table_info(sessions)')}
            if not {'id','updated_at','message_count'}.issubset(columns):raise StateError('unsupported_native_schema')
            rows=c.execute('SELECT id,updated_at,message_count FROM sessions WHERE parent_session_id IS NULL ORDER BY updated_at DESC,id LIMIT ?', (limit,)).fetchall() if 'parent_session_id' in columns else c.execute('SELECT id,updated_at,message_count FROM sessions ORDER BY updated_at DESC,id LIMIT ?', (limit,)).fetchall()
            integrity=c.execute('PRAGMA quick_check(1)').fetchone()[0]
            if integrity!='ok':raise StateError('native_integrity_failed')
        sessions=[]
        for sid,updated,count in rows:
            if not isinstance(sid,str) or not ID.fullmatch(sid):raise StateError('invalid_native_session')
            sessions.append({'session_id':sid,'updated_at':updated,'message_count':count})
        return {'status':'ok','sessions':sessions,'integrity':'ok','storage':storage_size(candidates[0])}
    except sqlite3.Error:raise StateError('native_store_unavailable') from None


def inspect(directory,limit=20):
    try:return native_sessions(directory,limit)
    except StateError as e:return {'status':'degraded','error':str(e),'sessions':[],'integrity':'not_verified'}


def perform(action,directory,project,root,session_id=None,limit=20):
    if action=='clear-cache':
        Cache(root).clear();return {'ok':True,'native_history_untouched':True}
    state=inspect(directory,100 if action in ('checkpoint','resume') else limit)
    if action=='status':
        cache=cache_overview(root)
        return {'ok':state['status']!='degraded' and cache['status']!='degraded','native':state,'cache':cache,'cache_contains':'session metadata only','history_encrypted':False,'credentials_in_cache':False}
    if action=='list':return {'ok':state['status']!='degraded',**state}
    if state['status']!='ok':raise StateError(state.get('error','no_native_session'))
    valid={s['session_id'] for s in state['sessions']}
    if action=='checkpoint':
        selected=session_id or (state['sessions'][0]['session_id'] if state['sessions'] else None)
        if selected not in valid:raise StateError('session_not_found')
        Cache(root).put(project,selected)
        return {'ok':True,'session_id':selected,'history_copied':False}
    if action=='resume':
        cached=Cache(root).get(project)
        selected=session_id or (cached['session_id'] if cached else None)
        if selected not in valid:raise StateError('session_not_found')
        # Return argv, never spawn/attach a writer to an existing user session.
        return {'ok':True,'session_id':selected,'argv':['harmonika','--cwd',str(Path(project).resolve()),'--data-dir',str(Path(directory).resolve()),'-s',selected],'automatic_attach':False}
    raise StateError('invalid_action')


def main():
    parser=argparse.ArgumentParser(description='Harmonika session metadata; native history stays authoritative.')
    parser.add_argument('action',choices=['status','list','checkpoint','resume','clear-cache'])
    parser.add_argument('--project',default=os.getcwd());parser.add_argument('--data-dir')
    parser.add_argument('--offline',action='store_true',help='Lewati pembacaan metadata engine remote')
    parser.add_argument('--format',choices=['json','text'],default='json');parser.add_argument('--session-id');parser.add_argument('--limit',type=int,default=20)
    args=parser.parse_args()
    if args.format=='text' and args.action!='status':parser.error('--format text hanya untuk status')
    root=Path(os.environ.get('HARMONIKA_CONFIG_DIR',str(Path.home()/'.config/harmonika')))/'state'
    directory=args.data_dir or str(Path(args.project)/'.harmonika')
    try:
        result=perform(args.action,directory,args.project,root,args.session_id,args.limit)
        if args.action=='status':result['engine_discovery']={'status':'offline'} if args.offline else engine_discovery_status()
        print(render_status(result) if args.format=='text' else json.dumps(result,ensure_ascii=False));return 0 if result.get('ok') else 2
    except (StateError,OSError):
        # No raw sqlite exceptions, paths, SQL, chat content or keys in error output.
        error=sys.exc_info()[1];code=str(error) if isinstance(error,StateError) else 'state_unavailable'
        print(json.dumps({'ok':False,'error':code,'native_history_untouched':True}));return 2

if __name__=='__main__':sys.exit(main())
