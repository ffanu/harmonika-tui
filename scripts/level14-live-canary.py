#!/usr/bin/env python3
"""Linux post-activation coding canaries, synthetic principal and data only.

Run locally on the main backend. The bridge secret stays in RAM and headers.
This operator-loopback test is distinct from public-token authentication tests.
"""
import hashlib,json,sqlite3,time,urllib.request,urllib.error
from pathlib import Path

def main():
    root=Path('/opt/harmonika-ai/level14-evidence-stage');out=root/'level14-live-canary.json'
    if out.exists():raise RuntimeError('existing_canary_reconcile_first')
    secret=Path('/etc/harmonika-ai/coding-runtime.token').read_text().strip()
    principal='api-token:149'+str(int(time.time()))
    with sqlite3.connect('/var/lib/harmonika-coding/state.sqlite') as db:
        if db.execute('SELECT count(*) FROM workspaces WHERE actor=?',(principal,)).fetchone()[0]:raise RuntimeError('canary_principal_collision')
    results=[]
    def call(method,path,body=None,key=None,owner=None):
        headers={'Content-Type':'application/json','X-Harmonika-Coding-Secret':secret,'X-Harmonika-Principal':owner or principal}
        if key:headers['Idempotency-Key']=key
        req=urllib.request.Request('http://127.0.0.1:18187'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
        start=time.monotonic()
        try:
            with urllib.request.urlopen(req,timeout=200) as res:status=res.status;raw=res.read(900000)
        except urllib.error.HTTPError as e:status=e.code;raw=e.read(900000)
        if body and body.get('stream') and status==200:
            text=raw.decode();assert text.endswith('data: [DONE]\n\n'),'sse_done_missing'
            events=[json.loads(l[6:]) for l in text.splitlines() if l.startswith('data: {')]
            value=next(x['data'] for x in reversed(events) if x.get('type')=='response.completed')
            results.append({'check':'actual_sse_done','passed':True})
        else:value=json.loads(raw)
        return status,value,round((time.monotonic()-start)*1000)
    status,caps,_=call('GET','/capabilities');assert status==200 and caps['features']['action_evidence'] and caps['features']['local_client_receipts_trusted'] is False
    results.append({'check':'coding_feature_ready','passed':True,'skills':len(caps['skills'])})
    status,value,_=call('POST','/workspaces',{'name':'Level14 release canary','permissions':{'write':True,'execute':True}});assert status==201;wid=value['workspace']['id'];route='/workspaces/'+wid
    text='Bukti sintetis: jumlah 42.\n'
    status,job,_=call('POST',route+'/invoke',{'skill_id':'fs.write','input':{'path':'notice.txt','content':text}},'l14-canary-seed');assert status==200 and job['status']=='completed'
    body={'messages':[{'role':'user','content':'Baca notice.txt; laporkan isi aktual. Jangan tulis atau eksekusi apa pun.'}],'verification':{'profile':'read','paths':['notice.txt']},'stream':True}
    status,value,elapsed=call('POST',route+'/agent',body,'l14-canary-read');assert status==200 and value['status']=='completed' and text.strip() in value['text'] and value['proof']['provenance']=='independently_verified'
    results.append({'check':'actual_readback_not_model_fiction','passed':True,'latency_ms':elapsed,'revision':value['proof']['revision']})
    status,value,_=call('POST',route+'/agent',body,'l14-canary-read');assert status==200 and value['replayed'] is True and value['proof']['workspace_version']==1
    results.append({'check':'same_key_replay_no_second_write','passed':True})
    status,_,_=call('GET',route,owner='api-token:149999999999999999');assert status==404
    results.append({'check':'other_principal_workspace_denied','passed':True})
    status,_,_=call('POST',route+'/agent',{'messages':[{'role':'user','content':'Read synthetic file'}],'verification':{'profile':'read','paths':['notice.txt'],'owner':'forged'}},'l14-invalid-owner');assert status==400
    results.append({'check':'owner_body_rejected','passed':True})
    for profile,prompt in [('conversation','Jelaskan singkat fungsi DNS dalam satu kalimat.'),('plan_only','Beri rencana singkat memeriksa proyek; jangan menjalankan tool atau mengubah berkas.')]:
        status,value,elapsed=call('POST',route+'/agent',{'messages':[{'role':'user','content':prompt}],'verification':{'profile':profile}},'l14-'+profile)
        assert status==200 and value['status']=='answered' and not value.get('steps') and value['text'].strip()
        if profile=='conversation':assert 'dns' in value['text'].lower() or 'domain' in value['text'].lower()
        results.append({'check':profile+'_no_actions','passed':True,'latency_ms':elapsed})
    status,value,_=call('GET',route);assert status==200 and value['workspace']['version']==1
    # Exercise the deployed phase2 image as well as HTTP/read paths. The test is
    # synthetic, unchanged, network-free, and approval is by this operator only.
    tests="import unittest,sqlite3,json\nfrom decimal import Decimal\nclass Original(unittest.TestCase):\n def test_runtime(self):\n  d=sqlite3.connect(':memory:');d.execute('CREATE TABLE items(id INTEGER)');d.execute('INSERT INTO items VALUES(42)');self.assertEqual(d.execute('SELECT id FROM items').fetchone()[0],42);d.close();self.assertEqual(json.loads('{\"a\":42}')['a'],42);self.assertEqual(Decimal('0.1')+Decimal('0.2'),Decimal('0.3'))\n"
    status,value,_=call('POST',route+'/invoke',{'skill_id':'fs.write','input':{'path':'test_acceptance.py','content':tests}},'l14-canary-test-seed');assert status==200 and value['status']=='completed'
    body={'messages':[{'role':'user','content':'Jalankan test_acceptance.py dengan test_run python_unittest. Jangan ubah berkas. Operator akan menyetujui job.'}],'verification':{'profile':'tests','tests':['test_acceptance.py'],'new_task':True}}
    status,value,_=call('POST',route+'/agent',body,'l14-canary-test');assert status==200 and value['status']=='approval_required'
    job=value['job'];assert job['skill_id']=='test.run' and job['workspace_id']==wid
    status,_,_=call('POST','/jobs/'+job['id']+'/approve',{'decision':'approve','input_hash':job['input_hash']});assert status==200
    for _ in range(120):
        status,finished,_=call('GET','/jobs/'+job['id']);assert status==200
        if finished['status'] not in ('queued','running'):break
        time.sleep(.2)
    assert finished['status']=='completed'
    status,value,_=call('POST',route+'/agent',{'messages':[{'role':'user','content':'Lanjutkan dari checkpoint yang disetujui; verifikasi hasil tes asli tanpa mengubah berkas.'}],'verification':{'task_id':value['proof']['task_id']}},'l14-canary-test-resume')
    assert status==200 and value['status']=='completed' and value['proof']['metrics']['tests']['collected']==1
    results.append({'check':'deployed_image_approved_test_and_independent_verifier','passed':True,'collected':1,'approval_source':'operator'})
    report={'passed':all(x['passed'] for x in results),'checks':results,'principal_provenance':'operator authenticated private loopback; public token derivation separately tested in Go','operator_principal':principal,'workspace_id':wid,'source_sha256':{n:hashlib.sha256((Path('/opt/harmonika-ai/coding_runtime')/n).read_bytes()).hexdigest() for n in ('agent.py','core.py','service.py','sandbox.py','action_evidence.py')},'raw_chat_or_credentials_logged':False}
    out.write_text(json.dumps(report,indent=2));out.chmod(0o600);print(json.dumps(report))

if __name__=='__main__':main()
