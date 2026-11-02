import sys,json
from pathlib import Path
sys.path.insert(0,'/Users/id/Projects/hai-studio-20261101');from remote import connect
base=Path('/Users/id/Projects/harmonika-tui')
with connect() as c:
 code='''import sqlite3,json
from pathlib import Path
c=sqlite3.connect('/var/lib/harmonika-flex-video/mechanics.sqlite');assert c.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]==0;c.close()
p=Path('/var/lib/harmonika-flex-video/synthetic-qualification.json');v=json.loads(p.read_text());result=json.loads((p.parent/v['job_id']/'result.json').read_text());assert result['duration_seconds']==10;print(json.dumps(result))
'''
 i,o,e=c.exec_command('python3 -',timeout=15);i.write(code);i.channel.shutdown_write();raw=o.read().decode();print(raw);assert o.channel.recv_exit_status()==0
 (base/'work/video-duration-20261101/rtx-result.json').write_text(raw)
 with c.open_sftp() as f:
  for n in ('engine.py','server.py'):f.put(str(base/'backend/flex_video'/n),'/opt/harmonika-ai/flex-video/'+n)
  v=json.loads(f.open('/var/lib/harmonika-flex-video/synthetic-qualification.json').read())
  f.get('/var/lib/harmonika-flex-video/'+v['job_id']+'/result.mp4',str(base/'work/video-duration-20261101/rtx-10s.mp4'))
 i,o,e=c.exec_command('systemctl restart harmonika-flex-video.service && systemctl is-active harmonika-flex-video.service',timeout=15);i.channel.shutdown_write();print(o.read().decode());print(e.read().decode());assert o.channel.recv_exit_status()==0
