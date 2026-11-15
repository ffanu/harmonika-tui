#!/usr/bin/env python3
"""Seal genuine native invalid_tool_response reproductions and verified retests."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);r=parser.parse_args().root
    failures=[];results=[]
    for case in ('failed_test_report','syntax_report','zero_tests_report'):
        d=r/('before-'+case)/'run';v=json.loads((d/'initiative-native-result.json').read_text())
        assert not v['passed'] and v['client_exit']!=0 and v['read_only_files_unchanged']
        assert json.loads((d/'final-response-private.json').read_text())['text']=='invalid_tool_response'
        assert v['stdin_isolated'] and v['effective_request_contains_initial_prompt'] and not v['inherited_launcher_detected']
        failures.append({'case':case,'result':v,'error':'invalid_tool_response','original_stdout_sha256':sha(d/'initiative-native.jsonl')})
    for case in ('failed_test_report','syntax_report','zero_tests_report','arithmetic','audit_read_only'):
        d=r/('after-'+case)/'run';v=json.loads((d/'initiative-native-result.json').read_text())
        assert v['passed'] and v['client_exit']==0 and v['manual_followups']==0 and v['protected_files_unchanged']
        assert v['stdin_isolated'] and v['effective_request_contains_initial_prompt'] and not v['inherited_launcher_detected']
        assert v['temporary_provider_reference_removed']
        if case!='arithmetic':assert v['read_only_files_unchanged'] and v['read_only_tree_unchanged']
        results.append(v)
    review=json.loads((r/'answer-review.json').read_text());assert review['all_accepted'] and len(review['cases'])==5
    for case,v in review['cases'].items():
        assert v['accepted'] and sha(r/('after-'+case)/'run/final-response-private.json')==v['response_sha256']
    before=json.loads((r/'before-guard.json').read_text());after=json.loads((r/'installed-guard.json').read_text())
    assert len(before['rows'])==len(after['rows'])==3 and not any(x['accepted'] for x in before['rows']) and all(x['accepted'] for x in after['rows'])
    tests={}
    for name in ('local-tests','linux-tests'):
        p=r/'final-tests'/(name+'.txt');text=p.read_text()
        counts={k:int(re.search(r'^# '+k+r' (\d+)$',text,re.M).group(1)) for k in ('tests','pass','fail','skipped')}
        assert counts['tests']==counts['pass'] and counts['fail']==counts['skipped']==0
        tests[name]={**counts,'log_sha256':sha(p)}
    p=r/'final-tests/activation-guards.txt';assert re.search(r'Ran 9 tests.*?\n\nOK',p.read_text(),re.S)
    tests['activation_guards']={'passed':9,'failed':0,'log_sha256':sha(p)};tests['mac_linux_counts_overlap_do_not_add']=True
    names=('receipt-outcome.cjs','verification-scope.cjs','task-continuity.cjs','decision-recovery.cjs','agent-controller.cjs','internal-tool-planner.js')
    hashes={n:sha(Path('backend/planner')/n) for n in names};stage=(r/'stage.txt').read_text().strip()
    code='''import pathlib,sys,json,hashlib,subprocess,urllib.request,datetime
v=json.load(sys.stdin);stage=pathlib.Path(v['stage']);base=pathlib.Path('/opt/harmonika-ai/api')
m={n:json.loads((stage/n).read_text()) for n in ['level18-candidate.json','level18-attestations.json','activation.json','level18-http-canary.json','verification.json','expected-baseline.json']}
e=m['level18-candidate.json'];a=m['level18-attestations.json'];assert e['passed'] and e['passed_count']==e['total']==12
assert all(x['passed'] and x['receipt_faults']==1 and x['scope_denials']==0 and x['write_replays']==0 and x['protected_files_unchanged'] for x in e['rows'])
assert a['all_verified'] and len(a['attestations'])==12 and not a['signing_key_exported']
assert m['activation.json']['status']=='active_pending_postcheck' and m['level18-http-canary.json']['passed']
actual={n:hashlib.sha256((base/n).read_bytes()).hexdigest() for n in v['hashes']}
assert actual==v['hashes']==e['source_sha256']==m['verification.json']['candidate_sha256']==m['activation.json']['candidate_sha256']
with urllib.request.urlopen('http://127.0.0.1:17890/health',timeout=5) as response:health=response.status
assert health==200
pids={u:int(subprocess.check_output(['systemctl','show','-p','MainPID','--value',u])) for u in v['pids']}
assert all(pids[u]==pid for u,pid in v['pids'].items() if u!='harmonika-ai')
m['release']={'status':'active_verified','verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_sha256':actual,'health_http':health,'service_pids':pids,'unrelated_service_pids_unchanged':True,'native_postchecks_passed':5,'regression_missions_passed':12}
p=stage/'invalid-response-release-verification.json';p.write_text(json.dumps(m['release'],indent=2));p.chmod(0o600)
print(json.dumps(m))
'''
    wrapper='import io,sys,json;v=json.load(sys.stdin);sys.stdin=io.StringIO(json.dumps(v["payload"]));exec(v["code"])'
    payload={'stage':stage,'hashes':hashes,'pids':json.loads((r/'pre-activation-state.json').read_text())['service_pids']}
    x=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','vps','python3 -c '+shlex.quote(wrapper)],input=json.dumps({'code':code,'payload':payload}),capture_output=True,text=True,check=True)
    meta=json.loads(x.stdout);private=Path.home()/'.local/state/harmonika-exams/invalid-tool-response-20261115'
    private.mkdir(mode=0o700,parents=True,exist_ok=True);private.chmod(0o700);manifest=[]

    def save(name,value):
        p=private/name;p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');p.chmod(0o600)
        manifest.append({'file':name,'sha256':sha(p)})

    for n,v in meta.items():save(n if n.endswith('.json') else n+'.json',v)
    for n,v in [('native-before.json',failures),('native-after.json',results),('answer-review.json',review),('before-guard.json',before),('installed-guard.json',after),('tests.json',tests)]:save(n,v)
    archives={n+'.txt':r/'final-tests'/(n+'.txt') for n in ('local-tests','linux-tests','activation-guards')}
    for phase in ('before','after'):
        for case in ('failed_test_report','syntax_report','zero_tests_report'):
            archives[phase+'-'+case+'-stdout.jsonl']=r/(phase+'-'+case)/'run/initiative-native.jsonl'
    for name,source in archives.items():
        dest=private/name;shutil.copyfile(source,dest);dest.chmod(0o600)
        manifest.append({'file':name,'sha256':sha(dest)})
    opencode={'reviewer':'OpenCode','read_only':True,'claude_involved':False,'log_sha256':sha(r/'opencode-review.jsonl'),
        'findings_addressed':['report verb/readonly phrase gaps','non-test syntax-check findings','zero-test summary reporting','repair request cannot be erased by read-only-report exception'],
        'boundary':'Non-test reporting restricted to recognized node/npm diagnostics, not arbitrary failed commands.'}
    report={'name':'Perbaikan invalid_tool_response pada laporan diagnostik sah','status':meta['release']['status'],'verified_at':meta['release']['verified_at'],
        'roles':{'Codex':'native reproduction, implementation, grading and activation','OpenCode':'read-only review','Claude':'not involved'},
        'genuine_native_failures':failures,'native_after':{'planned':5,'passed':5,'results':results,'answers_reviewed':review},
        'root_cause':'The controller required successful-completion evidence for terminal phases even when the authorized outcome was an honest read-only report of failed checks or zero tests. The audit answer exception excluded reporting verbs and syntax-check receipts; recovery repeated the positive-count requirement.',
        'changes':['Allow legacy phase=answer for correlated complete findings in explicit read-only reporting tasks.',
            'Recognize failed node --check/npm check diagnostics as observed findings without treating them as test success, worker termination or mutation authorization.',
            'Report a complete zero-test summary honestly; never fabricate tests or claim passing checks from zero counts.',
            'Recovery instructions distinguish reporting findings from proving successful completion.',
            'Repair intent anywhere in the command is preserved; negative repair wording and supported read-only phrases are handled separately.'],
        'preserved':['partial/pending/unknown operation and batch correlation protections','positive-test and post-write verification gates','operator-signed proof requirements','Data Firewall and executor authorization'],
        'guard_before_after':{'legitimate_reports_rejected_before':3,'accepted_by_installed_engine':3,'synthetic_checker_only':True,'model_called':False,'tools_executed':False},
        'tests':tests,'same_source_model_regressions':meta['level18-candidate.json'],'http_postcheck':meta['level18-http-canary.json'],
        'activation':{**meta['activation.json'],**meta['release']},'review':opencode,
        'scope':{'only_service_restarted':'harmonika-ai','bgp_core_vyos_billing_cs_rtx_modified':False,'existing_user_sessions_or_settings_changed':False,'github_commit_or_push':False,'synthetic_fixtures_only':True},
        'privacy':{'raw_customer_data_or_user_history_used':False,'raw_synthetic_transcripts_published':False,'temporary_provider_references_removed':True,'evidence':'mechanical counts/enums/hashes only'},
        'limits':['Fix verified for these reproducible diagnostic/reporting cases; not a guarantee against every future invalid_tool_response.',
            'Legacy receipts remain client_reported; observed diagnostic errors do not prove process termination or issue independent attestation.',
            'Command/intent classification is bounded and does not authorize shell operations or parse every natural-language phrasing.',
            'Native fixtures rely on trusted local clients and protected-file hashes, not a hostile-code sandbox.',
            'Model weights and cross-session databases were not trained or changed.'],
        'evidence':{'private_directory':str(private),'manifest':manifest}}
    out=Path('docs/INVALID-TOOL-RESPONSE-HUNT-20261115.json');out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');save(out.name,report)
    print(json.dumps({'status':report['status'],'native_failures_reproduced':3,'native_retests_passed':5,'report':str(out.resolve()),'sha256':sha(out)}))


if __name__=='__main__':main()
