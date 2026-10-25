"""Deterministic owner-authorized acceptance; operates only in new isolated fixtures."""
import argparse,hashlib,json,os,sys,time,uuid,urllib.request,urllib.error
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('base',nargs='?',default='http://127.0.0.1:18188')
parser.add_argument('--report',default='/tmp/harmonika-phase2-smoke-result.json')
args=parser.parse_args();BASE=args.base
if BASE not in ('http://127.0.0.1:18188','http://127.0.0.1:18187','https://chat.harmonika.id/v1/coding'):parser.error('only trusted Harmonika staging/production destinations are allowed')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**kw):raise ValueError('redirect_refused')
OPENER=urllib.request.build_opener(NoRedirect())
PRIVATE_FILE=os.environ.get('HARMONIKA_PHASE2_BEARER_FILE')
PUBLIC=json.loads(Path(PRIVATE_FILE).read_text()) if PRIVATE_FILE else []
AUTH={item['actor']:item['key'] for item in PUBLIC}
SECRET=Path('/etc/harmonika-ai/coding-runtime.token').read_text().strip() if not PUBLIC else None
SEED=900000000000000000+int(time.time());CHECKS=[];SKILLS=set();ACTORS=[];WORKSPACES=[]
EXPECTED=set('scaffold.project scaffold.component scaffold.api code.diff code.patch code.format format.run lint.run type.check build.run pkg.list pkg.install test.single test.coverage code.rename refactor.rename refactor.extract refactor.dead_code debug.attach debug.breakpoint'.split())
class Client:
 def __init__(self,actor):
  self.actor=actor;ACTORS.append(actor);self.execs=0
  self.w=self.call('POST','/workspaces',{'name':'Isolated Phase2 acceptance','permissions':{'write':True,'execute':True,'git_commit':True}})['workspace']['id'];WORKSPACES.append(self.w)
 def call(self,method,path,body=None,key=None,expected=200):
  h={'X-Harmonika-Coding-Secret':SECRET,'X-Harmonika-Principal':self.actor,'Content-Type':'application/json'}
  if AUTH:
   h={'Authorization':'Bearer '+AUTH[self.actor],'Content-Type':'application/json'}
  if key:h['Idempotency-Key']=key
  req=urllib.request.Request(BASE+path,method=method,headers=h,data=json.dumps(body).encode() if body is not None else None)
  try:
   with OPENER.open(req,timeout=165) as res:status=res.status;data=json.load(res)
  except urllib.error.HTTPError as e:status=e.code;data=json.load(e)
  assert status==expected or (expected==200 and status==201),(path,status,data)
  return data
 def invoke(self,skill,args,expected='completed',key=None):
  j=self.call('POST','/workspaces/'+self.w+'/invoke',{'skill_id':skill,'input':args},key or uuid.uuid4().hex)
  if j['status']=='approval_required':j=self.call('POST','/jobs/'+j['id']+'/approve',{'decision':'approve','input_hash':j['input_hash']})
  deadline=time.monotonic()+155
  while j['status'] in ('queued','running') and time.monotonic()<deadline:
   time.sleep(.1);j=self.call('GET','/jobs/'+j['id'])
  assert j['status']==expected,(skill,j)
  if expected=='completed':SKILLS.add(skill)
  CHECKS.append({'skill':skill,'expected':expected,'status':j['status']})
  return j
 def write(self,path,text):return self.invoke('code.write',{'path':path,'content':text})
 def read(self,path):return self.invoke('code.read',{'path':path})['result']
def result(j):return j['result']['tool_result']['result']
def text(j):return j['result']['stdout']
def check(name,condition):
 assert condition,name
 CHECKS.append({'check':name,'ok':True})
def main():
 for attempt in range(40):
  try:
   with OPENER.open(BASE+'/healthz',timeout=2) as response:
    if response.status==200:break
  except urllib.error.HTTPError as e:
   # The public gateway intentionally has no unauthenticated coding healthz.
   if AUTH:break
  except OSError:pass
  time.sleep(.25)
 a=Client(PUBLIC[0]['actor'] if PUBLIC else 'api-token:'+str(SEED));b=Client(PUBLIC[1]['actor'] if PUBLIC else 'api-token:'+str(SEED+1))
 c=a.call('GET','/capabilities');check('38 ready skills',len(c['skills'])==38 and all(x['readiness']['status']=='available' for x in c['skills']))
 a.invoke('scaffold.project',{'path':'starter','template':'python_http','name':'Acceptance'},key='starter-stable')
 old=a.call('GET','/workspaces/'+a.w)['workspace']['version']
 a.invoke('scaffold.project',{'path':'starter','template':'python_http','name':'Acceptance'},key='starter-stable')
 check('idempotent scaffold one publish',old==a.call('GET','/workspaces/'+a.w)['workspace']['version'])
 a.invoke('scaffold.component',{'path':'StatusCard.jsx','name':'StatusCard'})
 a.invoke('scaffold.api',{'path':'health.py','route':'/health','method':'GET'})
 a.write('calc.py','import os\n\ndef  add( x: int,y: int)-> int:\n return x+y\n')
 r=a.read('calc.py');j=a.invoke('code.diff',{'path':'calc.py','new_content':r['content']+'\n# acceptance\n'})
 a.invoke('code.patch',{'path':'calc.py','patch':j['result']['diff'],'expected_sha256':r['sha256']})
 a.invoke('code.format',{'path':'calc.py'});a.invoke('format.run',{'path':'calc.py'})
 r=a.read('calc.py');d=result(a.invoke('refactor.dead_code',{'path':'calc.py','apply':True,'expected_sha256':r['sha256']}));check('safe unused import removed','import os' not in a.read('calc.py')['content'])
 a.invoke('lint.run',{'path':'calc.py'});a.invoke('type.check',{'path':'calc.py'})
 py=result(a.invoke('build.run',{'path':'calc.py'}));check('real Python build artifact',py['artifact']['bytes']>0 and len(py['artifact']['sha256'])==64)
 jsx=result(a.invoke('build.run',{'path':'StatusCard.jsx'}));check('real React JSX bundle',jsx['artifact']['bytes']>0)
 a.write('use_calc.py','from calc import add\nassert add(20, 22) == 42\n# add is a comment\nLABEL = "add"\n')
 r=a.read('calc.py');lines=r['content'].splitlines();line=next(i+1 for i,v in enumerate(lines) if v.startswith('def add('))
 a.invoke('code.rename',{'path':'calc.py','line':line,'column':4,'new_name':'sum_values','expected_sha256':r['sha256']})
 content=a.read('use_calc.py')['content'];check('semantic cross-file import rename','from calc import sum_values' in content and '# add is a comment' in content and 'LABEL = "add"' in content)
 r=a.read('calc.py');a.invoke('refactor.rename',{'path':'calc.py','line':line,'column':4,'new_name':'total','expected_sha256':r['sha256']})
 a.write('transform.py','def process(value):\n    doubled = value * 2\n    result = doubled + 1\n    return result\n')
 check('extract before behavior',text(a.invoke('exec.python',{'code':'from transform import process; print(process(3))'})).strip()=='7')
 a.invoke('refactor.extract',{'path':'transform.py','start_line':2,'end_line':3,'name':'calculate'})
 check('extract after behavior',text(a.invoke('exec.python',{'code':'from transform import process; from calc import total; import use_calc; assert total(20,22)==42; print(process(3))'})).strip()=='7')
 a.write('test_calc.py','import unittest\nfrom calc import total\nclass CalcTest(unittest.TestCase):\n    def test_total(self): self.assertEqual(total(20,22),42)\n    def test_zero(self): self.assertEqual(total(0,0),0)\n')
 single=result(a.invoke('test.single',{'runner':'python_unittest','path':'test_calc.py','name':'test_calc.CalcTest.test_total'}));check('one real matched test',single['ran']==1 and single['passed'])
 cover=result(a.invoke('test.coverage',{'runner':'python_unittest','path':'.'}));check('real measured coverage',cover['passed'] and cover['test_count']>=2 and cover['totals']['num_statements']>0)
 a.write('bad.py','def broken() -> int:\n    return "incorrect"\n');a.invoke('type.check',{'path':'bad.py'},expected='failed')
 a.write('test_bad.py','import unittest\nclass Bad(unittest.TestCase):\n    def test_bad(self): self.assertEqual(1,2)\n');j=a.invoke('test.single',{'runner':'python_unittest','path':'test_bad.py','name':'test_bad.Bad.test_bad'},expected='failed');check('test failure never reported passed',result(j)['passed'] is False)
 # Second token covers independent offline environment and owned persistent DAP session.
 b.invoke('pkg.list',{'manager':'python'})
 for manager,name,version in [('python','humanize','4.12.2'),('node','decimal.js','10.5.0')]:b.invoke('pkg.install',{'manager':manager,'name':name,'version':version})
 check('offline Python imported in fresh container',text(b.invoke('exec.python',{'code':'import humanize; print(humanize.__version__)'})).strip()=='4.12.2')
 check('offline Node imported in fresh container',text(b.invoke('exec.node',{'code':"console.log(new (require('decimal.js'))(21).times(2).toString())"})).strip()=='42')
 b.write('debug_app.py',"value = 40\nvalue += 2\nopen('observed.txt', 'w').write(str(value))\nprint(value)\n")
 sid=b.invoke('debug.attach',{'action':'start','path':'debug_app.py'})['result']['debug_session']['id'];path='/debug/sessions/'+sid
 a.call('GET',path,expected=404);a.call('POST',path+'/stop',{},expected=404)
 b.call('POST','/workspaces/'+b.w+'/invoke',{'skill_id':'code.write','input':{'path':'blocked.py','content':'blocked'}},uuid.uuid4().hex,expected=409)
 bp=b.invoke('debug.breakpoint',{'session_id':sid,'path':'debug_app.py','lines':[3]})
 resumed=b.call('POST',path+'/actions',{'action':'continue'},'continue-once');check('real breakpoint line3',resumed['state']=='paused' and resumed['result']['location']['line']==3)
 replay=b.call('POST',path+'/actions',{'action':'continue'},'continue-once');check('debug continue replay',replay.get('replayed') is True)
 inspected=b.call('POST',path+'/actions',{'action':'inspect'},'inspect-once');check('inspect actual value42','"42"' in json.dumps(inspected['result']['variables']))
 b.call('POST',path+'/actions',{'action':'next'},'next-once')
 closed=b.call('POST',path+'/stop',{});check('debug Stop publishes valid snapshot',closed['state']=='closed' and closed['result']['workspace_updated'] and not closed['result']['changed_state_unknown'])
 b.call('POST',path+'/stop',{});check('debug output file42',b.read('observed.txt')['content']=='42')
 ev=b.call('GET',path+'/events');check('debug events recover terminal',any(x['type']=='debug.closed' for x in ev['events']))
 check('all20 new skills verified',EXPECTED<=SKILLS)
 return {'ok':True,'phase2_skills_verified':sorted(EXPECTED&SKILLS),'checks':CHECKS,'actors':ACTORS,'workspaces':WORKSPACES,'base':BASE,'verified_at':time.time(),'runtime_source_sha256':source_digest(),'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
def source_digest():
 folder=Path('/opt/harmonika-ai/coding-phase2-stage/coding_runtime' if BASE=='http://127.0.0.1:18188' else '/opt/harmonika-ai/coding_runtime')
 return hashlib.sha256(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob('*.py'))},sort_keys=True).encode()).hexdigest()
try:
 report=main();print(json.dumps(report,ensure_ascii=False));Path(args.report).write_text(json.dumps(report,ensure_ascii=False))
except BaseException as e:
 report={'ok':False,'error':str(e)[:12000],'checks':CHECKS,'actors':ACTORS,'workspaces':WORKSPACES};Path(args.report).write_text(json.dumps(report,ensure_ascii=False));print(json.dumps(report,ensure_ascii=False));raise
