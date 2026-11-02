import sys,time,json
sys.path.insert(0,'/Users/id/Projects/hai-studio-20261101');from remote import connect
code='''import json,sqlite3,time
from pathlib import Path
v=json.loads(Path('/var/lib/harmonika-flex-video/synthetic-qualification.json').read_text());c=sqlite3.connect('file:/var/lib/harmonika-flex-video/mechanics.sqlite?mode=ro',uri=True);r=c.execute('SELECT state,error,progress,created FROM jobs WHERE id=?',(v['job_id'],)).fetchone();print(json.dumps({'state':r[0],'error':r[1],'progress':r[2],'elapsed_seconds':round(time.time()-r[3])}));c.close()
'''
with connect() as c:
 for _ in range(90):
  i,o,e=c.exec_command('python3 -',timeout=15);i.write(code);i.channel.shutdown_write();out=o.read().decode().strip();print(out,flush=True)
  row=json.loads(out)
  if row['state'] not in ('queued','running'):break
  time.sleep(20)
