"""Authorized 18-skill public HTTPS acceptance on a synthetic order API project.

This deterministic client harness approves ONLY its own predeclared scenario jobs,
using the server-returned input_hash. It never disables backend approval guards,
uses a model to approve jobs, pushes Git, or touches an existing workspace.
Run locally where the private Harmonika key is already configured.
"""
import importlib.util
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('coding_client',ROOT/'runtime/tools/coding_cli.py')
client=importlib.util.module_from_spec(spec);spec.loader.exec_module(client)
OUT=Path.home()/'Downloads/Harmonika-18-skills-real-use-case';OUT.mkdir(exist_ok=True)
REPORT={'scenario':'Synthetic order quotation HTTP API: create, reproduce bug, repair, test, build, background report, Git','base_url':client.BASE,'started_at':time.time(),'calls':[],'status':'running'}
JOBS=[];WID=None
APP='''import json
from http.server import BaseHTTPRequestHandler, HTTPServer

def calculate_total(items):
    total = 0
    for item in items:
        price, quantity = item['price'], item['quantity']
        if type(price) is not int or price < 0 or type(quantity) is not int or quantity < 1:
            raise ValueError('invalid_item')
        total += price
    return total

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'ok': self.path == '/health'}).encode())
    def do_POST(self):
        try:
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            result = {'total': calculate_total(data['items'])}
            status = 200
        except (ValueError, KeyError, TypeError):
            result, status = {'error': 'invalid_order'}, 400
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

if __name__ == '__main__':
    HTTPServer(('127.0.0.1', 8080), Handler).serve_forever()
'''
TEST='''import json
import threading
import unittest
import urllib.request
import urllib.error
from http.server import HTTPServer
from app import Handler, calculate_total

ITEMS = [{'name':'Kabel','price':25000,'quantity':2}, {'name':'Konektor','price':5000,'quantity':3}]
class OrderTests(unittest.TestCase):
    def test_quantity_total(self):
        self.assertEqual(calculate_total(ITEMS), 65000)
    def test_empty(self):
        self.assertEqual(calculate_total([]), 0)
    def test_negative_quantity(self):
        with self.assertRaises(ValueError):
            calculate_total([{'price':1000,'quantity':-1}])
    def test_http_health(self):
        self.http_check('/health', None, 200, {'ok':True})
    def test_http_quote(self):
        self.http_check('/quote', {'items':ITEMS}, 200, {'total':65000})
    def test_http_invalid(self):
        self.http_check('/quote', {'items':[{'price':1000,'quantity':0}]}, 400, {'error':'invalid_order'})
    def http_check(self, path, body, status, expected):
        server=HTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.handle_request);worker.start()
        try:
            req=urllib.request.Request('http://127.0.0.1:%d%s'%(server.server_port,path),data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req,timeout=3) as r:code=r.status;value=json.load(r)
            except urllib.error.HTTPError as r:code=r.code;value=json.load(r);r.close()
            self.assertEqual(code,status);self.assertEqual(value,expected)
        finally:
            worker.join(timeout=4);server.server_close()
'''
ORDER={'items':[{'name':'Kabel','price':25000,'quantity':2},{'name':'Konektor','price':5000,'quantity':3}]}
NODE="const fs=require('fs'); const order=JSON.parse(fs.readFileSync('data/order.json','utf8')); const total=order.items.reduce((n,x)=>n+x.price*x.quantity,0); if(total!==65000)throw new Error('bad total'); console.log(JSON.stringify({items:order.items.length,total}));\n"

def save():
    p=OUT/'report.json';p.write_text(json.dumps(REPORT,ensure_ascii=False,indent=2)+'\n');p.chmod(0o600)
def request(method,path,body=None,key=None):
    value=client.request(method,path,body,key)
    if value.get('error') and not value.get('id'):raise RuntimeError(json.dumps(value))
    return value

def invoke(skill,args,expected='completed'):
    start=time.monotonic();key='realcase-'+uuid4().hex
    value=request('POST','/workspaces/'+WID+'/invoke',{'skill_id':skill,'input':args},key)
    assert 'id' in value,(skill,value);JOBS.append(value['id'])
    record={'skill_id':skill,'input':args,'job_id':value['id'],'initial_status':value['status'],'expected_status':expected};REPORT['calls'].append(record);save()
    if value['status']=='approval_required':
        # Exact fixture operation only; the user requested live testing of all18.
        assert skill in ('exec.shell','exec.python','exec.node','exec.background','test.run','git.commit')
        assert value['skill_id']==skill and value['workspace_id']==WID
        approved=request('POST','/jobs/'+value['id']+'/approve',{'decision':'approve','input_hash':value['input_hash']})
        record['approval_gate_verified']=True;record['approval_status']=approved['status']
    while value['status'] in ('approval_required','queued','running'):
        if time.monotonic()-start>160:raise RuntimeError('job_deadline')
        time.sleep(.12);value=request('GET','/jobs/'+value['id'])
    record['seconds']=round(time.monotonic()-start,3);record['response']=value;record['pass']=value['status']==expected
    if skill.startswith('exec.') or skill.startswith('git.') or skill=='test.run':
        record['events']=request('GET','/jobs/'+value['id']+'/events')['events']
    save();assert record['pass'],(skill,value)
    print(skill+' '+value['status']+' '+str(record['seconds'])+'s',flush=True)
    return value.get('result') or {}

try:
    caps=request('GET','/capabilities');assert caps['ready'];REPORT['expected_skills']=[x['id'] for x in caps['skills']]
    w=request('POST','/workspaces',{'name':'Real use case - Order API','permissions':{'write':True,'execute':True,'git_commit':True}})['workspace'];WID=w['id'];REPORT['workspace_id']=WID;save()
    invoke('fs.write',{'path':'README.md','content':'# Order API acceptance project\nSynthetic data only. Run python3 -m unittest discover.\n'})
    invoke('fs.write',{'path':'.gitignore','content':'__pycache__/\n*.pyc\n'})
    invoke('fs.write',{'path':'data/order.json','content':json.dumps(ORDER)})
    assert json.loads(invoke('fs.read',{'path':'data/order.json'})['content'])==ORDER
    invoke('code.write',{'path':'app.py','content':APP})
    invoke('code.write',{'path':'test_app.py','content':TEST})
    invoke('code.write',{'path':'client_check.js','content':NODE})
    listed=invoke('fs.list',{});assert any(e['name']=='app.py' for e in listed['entries'])
    tree=invoke('fs.tree',{'depth':3});assert 'order.json' in json.dumps(tree)
    glob=invoke('fs.glob',{'pattern':'*.py'});assert 'test_app.py' in json.dumps(glob)
    symbol=invoke('code.search',{'query':'calculate_total'});assert 'calculate_total' in json.dumps(symbol)
    grep=invoke('code.grep',{'pattern':'total += price','literal':True});assert 'app.py' in json.dumps(grep)
    invoke('git.commit',{'message':'test: baseline synthetic quotation API with regression fixture'})
    failed=invoke('test.run',{'runner':'python_unittest'},expected='failed');assert failed['exit_code']!=0 and '65000' in failed['stderr'];REPORT['regression_reproduced']=True
    source=invoke('code.read',{'path':'app.py'})
    invoke('code.edit',{'path':'app.py','old':'total += price','new':'total += price * quantity','expected_sha256':source['sha256']})
    status=invoke('git.status',{});assert 'app.py' in status['stdout']
    diff=invoke('git.diff',{});assert '+        total += price * quantity' in diff['stdout'];REPORT['verified_diff']=diff['stdout']
    tested=invoke('test.run',{'runner':'python_unittest'});assert tested['exit_code']==0 and 'Ran 6 tests' in tested['stderr'] and 'OK' in tested['stderr'];REPORT['unit_and_http_tests']=6
    py=invoke('exec.python',{'code':"import json;from app import calculate_total;o=json.load(open('data/order.json'));print(json.dumps({'total':calculate_total(o['items'])}))"});assert json.loads(py['stdout'])['total']==65000
    node=invoke('exec.node',{'path':'client_check.js'});assert json.loads(node['stdout'])['total']==65000
    shell=invoke('exec.shell',{'command':"python3 -c 'import ast,pathlib; ast.parse(pathlib.Path(\"app.py\").read_text()); print(\"SYNTAX_OK\")'"});assert 'SYNTAX_OK' in shell['stdout']
    bgcode="import json,time,pathlib;from app import calculate_total;print('REPORT_STARTED',flush=True);time.sleep(1);o=json.load(open('data/order.json'));pathlib.Path('reports').mkdir(exist_ok=True);pathlib.Path('reports/quote.json').write_text(json.dumps({'total':calculate_total(o['items'])}));print('REPORT_READY',flush=True)"
    import shlex
    bg=invoke('exec.background',{'command':'python3 -c '+shlex.quote(bgcode)});assert 'REPORT_READY' in bg['stdout']
    exported=invoke('fs.read',{'path':'reports/quote.json'});assert json.loads(exported['content'])['total']==65000
    invoke('git.commit',{'message':'fix: multiply quantity and verify API regression plus generated report'})
    final=invoke('git.status',{});assert not final['stdout'].splitlines()[1:],final
    for path in ('app.py','test_app.py','client_check.js','reports/quote.json','README.md','.gitignore','data/order.json'):
        value=invoke('fs.read',{'path':path});dest=OUT/path;dest.parent.mkdir(exist_ok=True);dest.write_text(value['content'])
    covered={x['skill_id'] for x in REPORT['calls'] if x['pass']};assert set(REPORT['expected_skills'])==covered
    REPORT.update(status='passed',skills_passed=len(covered),operation_count=len(REPORT['calls']),finished_at=time.time(),final_git_status=final['stdout'])
    save();print(json.dumps({'status':REPORT['status'],'skills_passed':len(covered),'operations':len(REPORT['calls']),'tests':6,'total':65000,'report':str(OUT/'report.json')}),flush=True)
except BaseException as e:
    REPORT.update(status='failed',error=str(e),finished_at=time.time());save();raise
finally:
    if REPORT['status']!='passed':
        for jid in JOBS:
            try:request('POST','/jobs/'+jid+'/stop',{})
            except Exception:pass
