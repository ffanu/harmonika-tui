"""Bind only our synthetic qualification artifact to the authenticated test owner."""
import sys,subprocess,json
sys.path.insert(0,'/Users/id/Projects/hai-studio-20261101');from remote import connect
js="""const {createClient}=require('./runtime/tools/video-owner-client.cjs');let signed;const c=createClient({fetch:async(u,o)=>{signed=JSON.parse(o.body);return new Response(JSON.stringify({ok:true}),{status:200})}});c.call('video_job_status',{job_id:'d04aeec3e6d80f0362a8cc2958ce8a18'}).then(()=>process.stdout.write(JSON.stringify(signed))).finally(()=>c.close());"""
signed=json.loads(subprocess.run(['node','-e',js],capture_output=True,text=True,check=True).stdout)
code='''import importlib.util,json,sqlite3
from pathlib import Path
s=importlib.util.spec_from_file_location('facade','/opt/harmonika-ai/video-routing/facade.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
data,owner=m.authorize(PROOF)
p=Path('/var/lib/harmonika-flex-video/synthetic-qualification.json');v=json.loads(p.read_text());assert v['job_id']==data['job_id']
c=sqlite3.connect('/var/lib/harmonika-flex-video/mechanics.sqlite');row=c.execute('SELECT owner,state FROM jobs WHERE id=?',(v['job_id'],)).fetchone();assert row==('a'*64,'completed')
c.execute('UPDATE jobs SET owner=? WHERE id=? AND owner=?',(owner,v['job_id'],'a'*64));c.commit();c.close();v['owner']=owner;p.write_text(json.dumps(v));print('synthetic_fixture_owner_bound')
'''.replace('PROOF',repr(signed))
with connect() as c:
 i,o,e=c.exec_command('python3 -',timeout=20);i.write(code);i.channel.shutdown_write();print(o.read().decode());print(e.read().decode()[:300]);assert o.channel.recv_exit_status()==0
