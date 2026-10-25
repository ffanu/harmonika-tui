"""Run on the engine host. Creates only isolated own test actors/workspaces."""
import json
from pathlib import Path
import time
import urllib.request
from uuid import uuid4
BASE='http://127.0.0.1:18187'
secret=Path('/etc/harmonika-ai/coding-runtime.token').read_text().strip()
A='api-token:900000000000000001';B='api-token:900000000000000002'
checks=[];jobs=[];spaces=[]
def call(method,path,body=None,who=A,key=None,expected=200):
    headers={'X-Harmonika-Coding-Secret':secret,'X-Harmonika-Principal':who,'Content-Type':'application/json'}
    if key:headers['Idempotency-Key']=key
    req=urllib.request.Request(BASE+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=165) as res:code=res.status;value=json.load(res)
    except urllib.error.HTTPError as e:code=e.code;value=json.load(e)
    assert code==expected,(path,code,value)
    return value

def invoke(skill,args,execute=False):
    value=call('POST','/workspaces/'+wid+'/invoke',{'skill_id':skill,'input':args},key=str(uuid4()));jobs.append(value['id'])
    if execute:
        if value['status']=='approval_required':
            assert value['requires_approval'];call('POST','/jobs/'+value['id']+'/approve',{'decision':'approve','input_hash':value['input_hash']})
        deadline=time.monotonic()+155
        while time.monotonic()<deadline:
            value=call('GET','/jobs/'+value['id'])
            if value['status'] not in ('queued','running'):break
            time.sleep(.15)
        assert value['status']=='completed',(skill,value)
    else:assert value['status']=='completed',(skill,value)
    checks.append(skill);return value

try:
    caps=call('GET','/capabilities');assert len(caps['skills'])==18
    wid=call('POST','/workspaces',{'name':'Isolated MVP acceptance','permissions':{'write':True,'execute':True,'git_commit':True}},expected=201)['workspace']['id'];spaces.append(wid)
    call('GET','/workspaces/'+wid,who=B,expected=404);checks.append('tenant_isolation')
    invoke('fs.write',{'path':'hello.txt','content':'Hello\n'})
    assert invoke('fs.read',{'path':'hello.txt'})['result']['content']=='Hello\n'
    invoke('code.write',{'path':'main.py','content':'print(40 + 2)\n'})
    read=invoke('code.read',{'path':'main.py'})['result']
    invoke('code.edit',{'path':'main.py','old':'40 + 2','new':'41 + 1','expected_sha256':read['sha256']})
    for skill,args in [('fs.list',{}),('fs.tree',{'depth':2}),('fs.glob',{'pattern':'*.py'}),('code.search',{'query':'print'}),('code.grep',{'pattern':'41','literal':True})]:invoke(skill,args)
    assert '42' in invoke('exec.python',{'path':'main.py'},True)['result']['stdout']
    assert '42' in invoke('exec.node',{'code':'console.log(42)'},True)['result']['stdout']
    isolation='''import os,socket\nassert os.getuid()==10000\nassert not os.path.exists('/var/run/docker.sock')\nassert not os.path.exists('/etc/harmonika-ai/developer-api.token')\ntry:\n open('/etc/forbidden','w').write('bad')\n raise AssertionError('rootfs writable')\nexcept OSError:pass\ns=socket.socket();s.settimeout(.5)\nassert s.connect_ex(('127.0.0.1',17890))!=0\nprint('ISOLATED')'''
    assert 'ISOLATED' in invoke('exec.python',{'code':isolation},True)['result']['stdout'];checks.append('real_isolation')
    invoke('exec.shell',{'command':'printf shell-ok > result.txt; printf shell-ok'},True)
    assert invoke('fs.read',{'path':'result.txt'})['result']['content']=='shell-ok';checks.append('tmpfs_roundtrip')
    invoke('exec.background',{'command':'printf background-ok'},True)
    invoke('code.write',{'path':'test_sample.py','content':'import unittest\nclass Sample(unittest.TestCase):\n def test_value(self):self.assertEqual(2+2,4)\n'})
    test=invoke('test.run',{'runner':'python_unittest'},True);assert 'OK' in test['result']['stderr']
    invoke('git.commit',{'message':'Isolated test commit'},True)
    invoke('git.status',{},True);invoke('git.diff',{},True)
    # The initial 18 operations must all have passed real execution/read/write.
    expected={x['id'] for x in caps['skills']};assert expected<=set(checks),(expected-set(checks))
    pending=call('POST','/workspaces/'+wid+'/invoke',{'skill_id':'exec.shell','input':{'command':'sleep 20','timeout_seconds':1}},key=str(uuid4()));jobs.append(pending['id'])
    call('POST','/jobs/'+pending['id']+'/approve',{'decision':'approve','input_hash':'wrong'},expected=409)
    call('POST','/jobs/'+pending['id']+'/approve',{'decision':'approve','input_hash':pending['input_hash']})
    for _ in range(300):
        value=call('GET','/jobs/'+pending['id'])
        if value['status'] not in ('queued','running'):break
        time.sleep(.1)
    assert value['status']=='timeout',value;checks.append('timeout')
    p=call('POST','/workspaces/'+wid+'/invoke',{'skill_id':'exec.shell','input':{'command':'echo started; sleep 60','timeout_seconds':60}},key=str(uuid4()));jobs.append(p['id'])
    call('POST','/jobs/'+p['id']+'/approve',{'decision':'approve','input_hash':p['input_hash']})
    time.sleep(1);call('POST','/jobs/'+p['id']+'/stop',{})
    for _ in range(100):
        value=call('GET','/jobs/'+p['id'])
        if value['status'] not in ('queued','running'):break
        time.sleep(.1)
    assert value['status']=='stopped',value;checks.append('stop')
    events=call('GET','/jobs/'+p['id']+'/events')['events'];assert events and all(x['sequence']>0 for x in events);checks.append('event_replay')
    value={'ok':True,'checks':checks,'count':len(checks),'workspaces':spaces,'job_ids':jobs}
    Path('/tmp/harmonika-coding-smoke-result.json').write_text(json.dumps(value,indent=2));print(json.dumps({'ok':True,'checks':checks,'count':len(checks)}))
except Exception:
    print(json.dumps({'ok':False,'checks':checks,'count':len(checks)}));raise
finally:
    for jid in jobs:
        try:call('POST','/jobs/'+jid+'/stop',{})
        except Exception:pass
