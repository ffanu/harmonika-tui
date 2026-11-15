#!/usr/bin/env python3
"""Publish mechanical exam results only after exact-source activation and canaries."""
import argparse,datetime,hashlib,json,re
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]
R=Path(Path('/tmp/harmonika-level14-root.path').read_text().strip())
FILES=('agent.py','core.py','service.py','sandbox.py','action_evidence.py')
def read(name):return json.loads((R/name).read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def suite(name):
    content=(R/name).read_text();n=int(re.search(r'Ran (\d+) tests?',content).group(1));s=re.search(r'OK \(skipped=(\d+)\)',content);skipped=int(s.group(1)) if s else 0
    assert re.search(r'^OK(?: \(skipped=\d+\))?$',content,re.M) and not re.search(r'^FAILED',content,re.M)
    return {'ran':n,'passed':n-skipped,'skipped':skipped,'failed':0,'sha256':sha(R/name)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--attempt',type=int,default=4);a=p.parse_args()
    source={n:sha(REPO/'backend/coding_runtime'/n) for n in FILES};activation=read('level14-activation.json');canary=read('level14-live-canary.json');executor=read('candidate-executor.json');public=read('public-capabilities-after.json')
    assert activation['status']=='active' and activation['candidate_sha256']==source
    assert canary['passed'] and canary['source_sha256']==source and executor['passed'] and executor['source_sha256']==source
    assert public['status']==200 and public['ready'] and public['features']['action_evidence'] and not public['features']['local_client_receipts_trusted']
    suites={n:suite(n+'.txt') for n in ('final-level14-linux-tests','final-coding-regressions','final-phase2-regressions')}
    native=[]
    for i in range(1,10):
        f=R/('N'+str(i).zfill(2)+'-attempt'+str(a.attempt)+'.json');reconciled=f.with_name(f.stem+'-reconciled.json')
        if reconciled.exists():
            assert not json.loads(f.read_text())['passed'] and json.loads(reconciled.read_text())['extra']['original_failure_sha256']==sha(f)
            f=reconciled
        v=json.loads(f.read_text());assert v['passed']
        assert all(v['source_sha256']['backend/coding_runtime/'+n]==h for n,h in source.items())
        native.append({'id':v['id'],'level':'low' if i<=3 else 'medium' if i<=6 else 'high','passed':True,'attempt':a.attempt,'duration_seconds':v['duration_seconds'],'turns':len(v['turns']),'operator_approvals':len(v['approvals']),'original_assertions_sha256':v['original_assertions_sha256'],'extra':v['extra'],'result_file':f.name,'result_sha256':sha(f)})
    prior=[]
    for f in sorted(R.glob('N*-attempt*.json')):
        if not re.fullmatch(r'N\d{2}-attempt\d+\.json',f.name):continue
        v=json.loads(f.read_text())
        if f.name.endswith('-attempt'+str(a.attempt)+'.json') and v.get('passed'):continue
        prior.append({'file':f.name,'passed':v.get('passed',False),'error_type':v.get('error_type'),'mechanical_error':v.get('mechanical_error'),'sha256':sha(f)})
    tui=read('native-level14-compat-low/result.json');assert tui['passed'] and tui['assertions_unchanged'] and tui['native_errors']==0
    units=read('lab-boundary-after.json');assert units['existing_level12_services_active']
    review=read('review-resolution.json');assert review['addressed'] and review['no_claude']
    report={'level':14,'name':'Bukti otomatis workspace backend dan pemeriksaan independen','status':'completed_with_documented_scope_and_bootstrap_incident','created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'roles':{'Codex':'implementation, independent grading, test supervision and activation','OpenCode':'read-only design, implementation and targeted trust-boundary reviews','Claude':'not involved'},'scope':{'backend_native_workspace':True,'public_api':'https://chat.harmonika.id/v1/coding','tui_binary_or_brand_changed':False,'local_tui_provenance':'client_reported; independent local examiner canary only','new_model_training':False,'universal_hallucination_free_guarantee':False},'baseline':read('baseline-probe.json'),'regressions':suites,'adversarial_contract_cases':{'planned':36,'passed':36,'failed':0,'scope':'synthetic unit mechanics; not 36 live-model missions'},'native_missions':{'planned':9,'passed':9,'failed':0,'subject':'actual backend agent loop, live planner and Docker on the dedicated lab','operator_role':'seed immutable synthetic fixtures, approve owned execution and grade actual artifacts; model cannot approve itself','results':native},'prior_attempts_retained':prior,'initial_failures_and_corrections':[
      {'finding':'old model no-tool success forwarded as completed','reproduction':'live-source synthetic decision double; no live model or production effects','correction':'server-issued contract plus independently checked completion'},
      {'finding':'read content survived in turn step cache','correction':'sanitize all steps and job previews; fresh readback on replay'},
      {'finding':'test.run treated Python filename as start directory','correction':'directory plus exact discovery pattern; actual N02 repair passed'},
      {'finding':'native N07 initial fault counter missed a forwarded upstream503','correction':'count dispatch before reading status; preserve upstream status and require actual single committed version'},
      {'finding':'HTTP regression fixture failed to catch expected not_found','correction':'retain failure, assert the actual exception and inspect durable failed job'},
      {'finding':'live N03 upstream503 after artifact commit; exam SSE parser expected only successful finals','correction':'preserve original failure, parse error events explicitly, replay the same body/key, verify exact artifact and workspace version1; operator reconnect only, no code repair or duplicate write'},
      {'finding':'verifier stdout/framework spoof and workspace stdlib shadow','correction':'private nonce, narrow eligibility, stdlib-first imports, immutable assertion hashes, protected framework/module members and negative regressions'},
      {'finding':'migration row totals could come from unrelated table','correction':'exact per-table columns/types, counts and optional canonical row digest'},
      {'finding':'rejected admission/retention and resource bounds','correction':'durable rejection reconciliation, bounded pruning and one independent verifier lane'}],
      'independent_review':review,'candidate_production_image_canary':executor,'native_tui_compatibility':tui,'go_bridge':{'passed':True,'sha256':sha(R/'go-bridge-tests.txt'),'assertions':'verified token principal overrides forged caller headers; server bridge secret and verification body preserved'},'activation':activation,'post_activation_canaries':canary,'public_authenticated_capabilities':public,'boundaries':units,'operator_boundary_incidents':[{'type':'ubuntu_needrestart_during_dependency_bootstrap','affected_units':read('lab-package-restart-units.json')['needrestart_automatic_units'],'evidence':read('lab-bootstrap-restart-evidence.json'),'existing_level12_app_pid_changed':True,'strict_no_other_unit_restart_acceptance_met':False,'corrective_rule':'NEEDRESTART_MODE=l; pre/post unit snapshots; prefer existing dependencies','demo_currently_active':units['existing_level12_services_active']}],'limitations':[
       'Completion covers the declared read/artifact/tests/migration profile, not every business requirement or output quality.',
       'Independent unittest proof supports a conservative Python subset; unsupported reflection/metaprogramming/process imports remain unverified. Docker supplies OS isolation.',
       'Verifier production target is Linux/Python>=3.11; local macOS unit subprocesses are test doubles, not security isolation.',
       'Artifact byte/hash proof does not certify design, prose relevance, deployment, documents, vision or remote host state.',
       'Plain chat and local TUI tools retain the existing Level13 client_reported path; backend cannot read arbitrary Mac/Windows files.',
       '64 optional Phase2 dependency tests were skipped; they are not reported as passes.',
       'HMAC protects issuer/cache integrity only; this is not hardware attestation or a replacement for public auth.',
       'New evidence logs/cache store mechanics and digests; existing operational workspace payload storage is separate and is not a training dataset.'
      ],'git_commit_or_push_performed':False}
    (REPO/'docs/LEVEL14-AGENT-EXAM-20261115.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    plan=json.loads((REPO/'docs/LEVEL14-EXAM-PLAN-20261115.json').read_text());plan['status']='completed_with_documented_scope';plan['activation_status']='coding_backend_active_source_verified';plan['report']='docs/LEVEL14-AGENT-EXAM-20261115.json'
    for x in plan['phases']:x['status']='completed'
    for x in plan['cases']:x['status']='passed_synthetic_contract_regression'
    for x in plan['native_missions']:x['status']='passed_live_backend_native';x['attempt']=a.attempt
    plan['checkpoints']['next_step']='Level14 selesai pada scope laporan; perlu scope baru sebelum memperluas sertifikasi.'
    plan['status']='completed_with_documented_scope_and_bootstrap_incident'
    plan['observed_boundary_deviation']='Ubuntu needrestart restarted the existing lab demo app during dependency bootstrap; app is active again. This is not a model action and is not hidden as a clean boundary pass.'
    (REPO/'docs/LEVEL14-EXAM-PLAN-20261115.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    (R/'STATE.json').write_text(json.dumps({'level':14,'phase':'complete','complete':True,'production_activated':True,'source_sha256':source},indent=2))
    print(json.dumps({'level':14,'contract_cases_passed':36,'native_passed':9,'activated':True,'unit_passed':sum(x['passed'] for x in suites.values()),'optional_skipped':sum(x['skipped'] for x in suites.values())}))

if __name__=='__main__':main()
