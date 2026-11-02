import sys
sys.path.insert(0,'/Users/id/Projects/hai-studio-20261101');from remote import connect
code='''import json,urllib.request
from pathlib import Path
v=json.loads(Path('/var/lib/harmonika-flex-video/synthetic-qualification.json').read_text());v.update(action='job')
r=urllib.request.Request('http://127.0.0.1:18258/rpc',data=json.dumps(v).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+Path('/etc/harmonika-ai/video-toolkit.token').read_text().strip()})
with urllib.request.urlopen(r,timeout=10) as x:print(json.dumps(json.load(x)))
'''
with connect() as c:
 i,o,e=c.exec_command('python3 -',timeout=15);i.write(code);i.channel.shutdown_write();print(o.read().decode());print(e.read().decode()[:500])
