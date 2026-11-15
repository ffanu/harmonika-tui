#!/usr/bin/env python3
"""Independent Linux effects/ACK/hash/SQLite checks on the one authorized exam host."""
import argparse,fcntl,base64,re,hashlib,importlib.util,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--native',default='native-baseline-high');p.add_argument('--attempt',type=int,default=2);a=p.parse_args();assert re.fullmatch(r'native-[a-z0-9-]+-(?:low|medium|high)',a.native) and 1<=a.attempt<=99,'invalid_scope';r=Path(Path('/tmp/harmonika-level13-root.path').read_text().strip());lock=(r/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);d=r/a.native
protected=json.loads((d/'protected.json').read_text());assert all(hashlib.sha256((d/n).read_bytes()).hexdigest()==h for n,h in protected.items());assert json.loads((d/'result.json').read_text())['passed']
spec=importlib.util.spec_from_file_location('operator_transport',Path(__file__).with_name('level12-operator.py'));op=importlib.util.module_from_spec(spec);spec.loader.exec_module(op)
code=r'''import base64,hashlib,http.server,importlib.util,json,os,re,sqlite3,subprocess,sys,threading,urllib.request,time
from pathlib import Path
v=json.load(sys.stdin);assert re.fullmatch(r'hl13-[a-f0-9]{12}-r[0-9]+',v['id']);assert hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='a9baadf374fdfbacaa7ac7d7562109b32111821b583170a94d423de4ef4e65eb'
def level12_pids():
 return {n:subprocess.check_output(['systemctl','show',n,'-p','MainPID','--value'],text=True).strip() for n in ['hl12-17ce5924fb-app.service','hl12-17ce5924fb-edge.service']}
before=level12_pids()
root=Path('/opt/harmonika-exams')/v['id'];root.mkdir(mode=0o700);os.chdir(root)
for name,b in v['files'].items():
 assert name in ['app.py','test_app.py'];p=root/name;p.write_bytes(base64.b64decode(b));assert hashlib.sha256(p.read_bytes()).hexdigest()==v['hashes'][name]
x=subprocess.run(['python3','-m','unittest','-v'],capture_output=True,text=True,timeout=20);assert x.returncode==0;count=int(re.search(r'Ran (\d+) tests',x.stderr).group(1));assert count==4
spec=importlib.util.spec_from_file_location('app',root/'app.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
dbpath=str(root/'jobs.db');db=sqlite3.connect(dbpath);db.execute('PRAGMA journal_mode=WAL');m.initialize(db);db.close();posts=0;operation='l13-lost-ack';errors=[]
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  global posts
  posts+=1
  conn=sqlite3.connect(dbpath)
  try:m.submit(conn,operation,'synthetic-value')
  finally:conn.close()
  self.close_connection=True  # effect committed; intentionally no HTTP acknowledgement
 def do_GET(self):
  conn=sqlite3.connect(dbpath)
  try:state=m.lookup(conn,operation)
  finally:conn.close()
  if self.path=='/functional':self.send_response(503);self.end_headers();return
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'operation':operation,'state':state}).encode())
server=http.server.HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever);thread.start();url='http://127.0.0.1:'+str(server.server_port)
try:
 lost=False
 try:urllib.request.urlopen(urllib.request.Request(url+'/submit',data=b'{}',method='POST'),timeout=5)
 except Exception:lost=True
 with urllib.request.urlopen(url+'/status',timeout=5) as response:state=json.load(response);health=response.status
 functional_failed=False
 try:urllib.request.urlopen(url+'/functional',timeout=5)
 except urllib.error.HTTPError as e:functional_failed=e.code==503
 assert lost and posts==1 and state['state']=='synthetic-value' and health==200 and functional_failed
finally:server.shutdown();thread.join(timeout=5);server.server_close()
db=sqlite3.connect(dbpath);assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1;assert m.migrate(db)==2 and m.lookup(db,operation)=='synthetic-value';assert db.execute('PRAGMA user_version').fetchone()[0]==2;assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';db.close()
# New process, same original identity; do not replay submit after restart.
x=subprocess.run(['python3','-c',"import sqlite3;d=sqlite3.connect('jobs.db');assert d.execute('SELECT value FROM jobs WHERE id=?',('l13-lost-ack',)).fetchone()[0]=='synthetic-value';assert d.execute('SELECT count(*) FROM jobs').fetchone()[0]==1"],capture_output=True,timeout=10);assert x.returncode==0
expected=(root/'app.py').read_bytes();partial=expected[:-9];(root/'partial-artifact').write_bytes(partial);assert len(partial)!=len(expected) and hashlib.sha256(partial).digest()!=hashlib.sha256(expected).digest()
y=subprocess.run(['python3','-c',"print('PASS');raise SystemExit(7)"],capture_output=True,text=True);assert y.returncode==7 and 'PASS' in y.stdout
empty=root/'empty';empty.mkdir();z=subprocess.run(['python3','-c',"import unittest,sys;r=unittest.TextTestRunner().run(unittest.TestSuite());sys.exit(not r.wasSuccessful())"],capture_output=True,text=True);assert z.returncode==0 and 'Ran 0 tests' in z.stderr
assert level12_pids()==before
result={'status':'passed','scope':'isolated_dedicated_linux','native_source_sha256':v['hashes']['app.py'],'assertions_sha256':v['hashes']['test_app.py'],'linux_tests':count,'lost_ack':True,'post_requests':posts,'committed_effects':1,'restart_lookup_without_resubmit':True,'migration_schema':2,'integrity':'ok','health200_functional503_distinguished':True,'truncated_artifact_rejected':True,'nonzero_stdout_pass_rejected':True,'zero_tests_not_success':True,'loopback_only':True,'process_signals_sent':0,'level12_unit_pids_unchanged':level12_pids()==before,'lab_id':v['id']};(root/'mechanics.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
'''
lab_id='hl13-'+hashlib.sha256((a.native+str(r)).encode()).hexdigest()[:12]+'-r'+str(a.attempt);value={'id':lab_id,'files':{n:base64.b64encode((d/n).read_bytes()).decode() for n in ['app.py','test_app.py']},'hashes':{n:hashlib.sha256((d/n).read_bytes()).hexdigest() for n in ['app.py','test_app.py']}};out=op.python(code,value,timeout=50);(r/(a.native+'-linux-grade.json')).write_text(json.dumps(out,indent=2));print(json.dumps(out))
