#!/usr/bin/env python3
import argparse,fcntl,hashlib,json,os,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--label',required=True);p.add_argument('--resume',action='store_true');p.add_argument('--prompt',type=Path,required=True);a=p.parse_args()
r=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());project=r/'project';lock=(r/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
mfile=r/'project-meta.json';meta=json.loads(mfile.read_text());assert all(hashlib.sha256((project/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items())
if a.resume:assert meta.get('native_session_id')
log=r/(a.label+'-native.jsonl');assert not log.exists();env=os.environ.copy()
for key in ['XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME']:
 d=r/key.lower();d.mkdir(mode=0o700,exist_ok=True);env[key]=str(d)
tmp=project/'.tmp';tmp.mkdir(mode=0o700,exist_ok=True);env['TMPDIR']=str(tmp)
cmd=['harmonika','run','--standalone','--auto','--format','json','--model','harmonika/harmonika-ai']
if a.resume:cmd+=['--session',meta['native_session_id']]
started=time.time()
with log.open('w') as out:
 log.chmod(0o600);proc=subprocess.Popen(cmd+[a.prompt.read_text()],cwd=project,env=env,stdout=out,stderr=subprocess.STDOUT);print(json.dumps({'label':a.label,'pid':proc.pid}),flush=True);code=proc.wait()
events=[]
for line in log.read_text().splitlines():
 try:events.append(json.loads(line))
 except ValueError:pass
sid=next((e.get('sessionID') for e in events if e.get('sessionID')),None)
if not a.resume:meta['native_session_id']=sid
mfile.write_text(json.dumps(meta,indent=2));result={'label':a.label,'started_at':started,'finished_at':time.time(),'exit_code':code,'same_session':bool(sid) and sid==meta.get('native_session_id'),'tool_count':sum(e.get('type')=='tool_use' for e in events),'native_errors':sum(e.get('type')=='error' for e in events)};(r/(a.label+'-run.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
