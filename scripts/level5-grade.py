#!/usr/bin/env python3
"""Independent Level5 grading; only registered ephemeral lab, synthetic data."""
import argparse,base64,hashlib,importlib.util,json,os,re,subprocess,sys,time
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def remote(root,req):
 path=json.loads((root/'lab.json').read_text())['remote_root']+'/controller.py'
 p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+path],input=json.dumps(req),capture_output=True,text=True,timeout=180)
 if p.returncode:raise RuntimeError('lab_control_failed')
 return json.loads(p.stdout)
def bundle(p):return {n:base64.b64encode((p/n).read_bytes()).decode() for n in ['app.py','web.html','identity_provider.py','Dockerfile','requirements.txt','vendor/harmonika_lab_contracts-1.0.0-py3-none-any.whl']}
def grade(root,v2=False,label='grade'):
 p=root/'project';meta=json.loads((root/'grade.json').read_text())
 protected_ok=all((p/n).is_file() and hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items())
 if not protected_ok:
  result={'phase':'v2' if v2 else 'v1','passed':False,'checks':{'protected_files':False},'tests':{},'execution_aborted':'protected_exam_file_changed'}
  (root/(label+'-result.json')).write_text(json.dumps(result,indent=2));return result
 status=remote(root,{'op':'status'});ledger=remote(root,{'op':'ledger'});db=remote(root,{'op':'db-audit'})
 (root/(label+'-remote.json')).write_text(json.dumps({'status':status,'ledger':ledger,'db':db},indent=2))
 family='app-v2' if v2 else 'app-v1';release=status['active_release'];checkpoint=json.loads((p/'CHECKPOINT.json').read_text()) if (p/'CHECKPOINT.json').exists() else {};expected=ledger['requests'].get(checkpoint.get('request_id'),{})
 checks={'protected_files':all((p/n).is_file() and hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items()),'active_release':bool(re.fullmatch(family+r'(?:-r[1-3])?',release or '')),'ready':status['readiness'].get('http_status')==200,'nonroot':status['isolation']['user']=='10001:10001','network_none':status['isolation']['network']=='none','readonly':status['isolation']['readonly'],'memory_bounded':status['isolation']['memory']==201326592,'cpu_bounded':status['isolation']['nano_cpus']==500000000,'pids_bounded':status['isolation']['pids_limit']==32,'caps_dropped':'ALL' in status['isolation']['cap_drop'],'no_new_privileges':any(x.startswith('no-new-privileges') for x in status['isolation']['security_opt']),'sqlite_integrity':db['integrity']=='ok','schema_version':db['schema_version']==(2 if v2 else 1),'source_matches_image':ledger['releases'].get(release,{}).get('source_sha256')==hashlib.sha256(json.dumps(bundle(p),sort_keys=True).encode()).hexdigest(),'image_matches_registry':status['image_id']==ledger['releases'].get(release,{}).get('image_id'),'checkpoint_exact':bool(re.fullmatch(r'stage-v1(?:-r[1-3])?',checkpoint.get('request_id',''))) and checkpoint=={'request_id':checkpoint.get('request_id'),**expected},'activation_ack_reconciled':ledger.get('activation_ack_reconciled') is True,'no_stage_v1_replay':ledger['activation_attempts'].get('stage-v1')==1,'brand_file_preserved':(p/'brand.txt').read_text().strip()=='Harmonika — Tiket Gangguan'}
 if v2:
  checks['checkpoint_preserved']=hashlib.sha256((p/'CHECKPOINT.json').read_bytes()).hexdigest()==meta['checkpoint_sha256']
  checks['user_edit_preserved']='<!-- USER-EDIT-L5: keep this marker -->' in (p/'web.html').read_text()
  checks['exact_activations']={'stage-v1','stage-v2','challenge','rollback'}<=set(ledger['requests']) and all(re.fullmatch(r'(?:stage-v[12](?:-r[1-3])?|challenge|rollback)',k) and ledger['activation_attempts'].get(k)==1 for k in ledger['requests'])
  observations=[x for x in ledger['observations'] if x['origin']=='agent']
  checks['agent_verified_bad_readiness']=ledger.get('bad_readiness_observed') is True
  sentinel=json.loads((root/'sentinel.json').read_text());actual=next((x for x in db['rows'] if x['id']==sentinel['ticket']['id']),None)
  checks['old_ticket_preserved']=actual is not None and all(actual[k]==sentinel['ticket'][k] for k in ['id','title','description','status']) and actual.get('priority')=='normal'
  checks['old_attachment_preserved']=any(x['id']==sentinel['attachment']['id'] and x['content_b64']==sentinel['content_b64'] for x in db['attachments'])
  wanted={'scope':'isolated_linux_docker','schema_version':2,'active_release':release,'same_session_resume':True,'rollback_performed':True,'uncertain_write_reconciled':True,'production_application_changed':False}
  checks['result_truthful']=(p/'RESULT.json').exists() and json.loads((p/'RESULT.json').read_text())==wanted
  checks['report_exists']=(p/'HASIL.md').is_file()
  proxy=json.loads((root/'proxy-mechanics.json').read_text());events=proxy['events'];drops=[i for i,e in enumerate(events) if e.get('ack_dropped') and e['origin']=='agent'];spec=importlib.util.spec_from_file_location('recovery_proof',REPO/'scripts/level5-lab/recovery_proof.py');proof=importlib.util.module_from_spec(spec);spec.loader.exec_module(proof);checks['agent_lost_ack_reconciled']=proof.is_reconciled(proxy)
  checks['agent_observed_database_busy']=ledger.get('db_busy_observed') is True
 tests={}
 for kind,args in [('public',[str(root/'examiner-venv/bin/python'),'-I','-c',"import sys,unittest;sys.path.insert(0,sys.argv[1]);suite=unittest.defaultTestLoader.discover('tests');result=unittest.TextTestRunner(verbosity=2).run(suite);sys.exit(not result.wasSuccessful())",str(p)]),('direct',[str(root/'examiner-venv/bin/python'),'-I',str(REPO/'scripts/level5-lab/direct-grade.py')])]:
  x=subprocess.run(args,cwd=p,capture_output=True,text=True,timeout=120);(root/(label+'-'+kind+'.txt')).write_text(x.stdout+x.stderr);tests[kind]={'passed':x.returncode==0};checks[kind]=x.returncode==0
 proxy=subprocess.Popen([sys.executable,str(REPO/'scripts/level5-lab/proxy.py'),str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 line=proxy.stdout.readline();url=json.loads(line)['url'];previous_url=os.environ.get('LEVEL5_URL');os.environ['LEVEL5_URL']=url;env=os.environ.copy();env['LEVEL5_URL']=url;env['LEVEL5_V2']='1' if v2 else '0';env['LEVEL5_EXAM_ORIGIN']='examiner';env['LEVEL5_BROWSER_MODULE']=json.loads((p/'tools/lab.json').read_text())['browser_module']
 try:
  for kind,args in [('api',[sys.executable,str(REPO/'scripts/level5-lab/acceptance.py')]),('browser',['node',str(REPO/'scripts/level5-lab/browser-grade.cjs')])]:
   x=subprocess.run(args,cwd=p,env=env,capture_output=True,text=True,timeout=480);(root/(label+'-'+kind+'.txt')).write_text(x.stdout+x.stderr);entry={'passed':x.returncode==0}
   if kind=='api':
    match=re.search(r'Ran (\d+) tests?',x.stderr);skip=re.search(r'skipped=(\d+)',x.stderr);entry.update(tests=int(match.group(1)) if match else None,skipped=int(skip.group(1)) if skip else 0)
   else:
    try:entry['result']=json.loads(x.stdout.splitlines()[-1])
    except (ValueError,IndexError):entry['result']={'invalid_report':True}
   tests[kind]=entry;checks[kind]=x.returncode==0
  if not v2 and checks['api'] and checks['browser']:
   # Test fixture persisted across the subsequent requirement change and rollback.
   spec=importlib.util.spec_from_file_location('acceptance',REPO/'scripts/level5-lab/acceptance.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ORIGIN='examiner';c=m.Client();c.login('alpha');payload={'title':'Migration sentinel','description':'Existing synthetic customer ticket'};code,item,_=c.request('/api/tickets',payload,headers={'Idempotency-Key':'migration-sentinel'});assert code in [200,201]
   body={'filename':'sentinel.txt','mime':'text/plain','content_base64':base64.b64encode(b'SYNTHETIC PERSISTENT ATTACHMENT').decode()};code,att,_=c.request('/api/tickets/'+str(item['id'])+'/attachments',body);assert code==201
   (root/'sentinel.json').write_text(json.dumps({'ticket':item,'attachment':att,'content_b64':body['content_base64'],'payload':payload},indent=2))
  if v2 and (root/'sentinel.json').exists():
   spec=importlib.util.spec_from_file_location('acceptance',REPO/'scripts/level5-lab/acceptance.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ORIGIN='examiner';c=m.Client();c.login('alpha');sentinel=json.loads((root/'sentinel.json').read_text());code,item,_=c.request('/api/tickets',sentinel['payload'],headers={'Idempotency-Key':'migration-sentinel'});checks['old_request_replays_without_new_row']=code==200 and item.get('id')==sentinel['ticket']['id']
 finally:
  if previous_url is None:os.environ.pop('LEVEL5_URL',None)
  else:os.environ['LEVEL5_URL']=previous_url
  proxy.stdin.write('shutdown\n');proxy.stdin.flush();proxy.stdin.close();proxy.wait(timeout=40);(root/(label+'-proxy-stderr.txt')).write_text(proxy.stderr.read())
 result={'phase':'v2' if v2 else 'v1','passed':all(checks.values()),'checks':checks,'tests':tests};(root/(label+'-result.json')).write_text(json.dumps(result,indent=2));return result
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--root',type=Path,required=True);a.add_argument('--v2',action='store_true');a.add_argument('--label',default='grade');n=a.parse_args();v=grade(n.root,n.v2,n.label);print(json.dumps(v));raise SystemExit(0 if v['passed'] else 1)
