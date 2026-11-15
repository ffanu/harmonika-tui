#!/usr/bin/env python3
"""Verify actual installed source and seal bounded initiative exam evidence."""
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    root = parser.parse_args().root
    stage = (root/'release-stage.txt').read_text().strip()
    plan = json.loads(Path('docs/INITIATIVE-COMMAND-EXAM-PLAN-20261115.json').read_text())
    cases = [json.loads((root/('native-'+n)/'initiative-native-result.json').read_text()) for n in plan['native_cases']]
    assert len(cases) == 6 and all(c['passed'] and c['client_exit'] == 0 and c['manual_followups'] == 0
        and c['protected_files_unchanged'] and c['temporary_provider_reference_removed']
        and c['stdin_isolated'] and c['effective_request_contains_initial_prompt']
        and not c['inherited_launcher_detected'] for c in cases)
    assert all(c['independent_test_exit'] == 0 and c['expected_tests_passed'] for c in cases if c['case'] not in ('audit_read_only','conflicting_evidence'))
    assert all(c['read_only_files_unchanged'] and not c['affirmative_unsupported_success_claim'] for c in cases if c['case'] in ('audit_read_only','conflicting_evidence'))
    heldout=json.loads((root/'heldout-verification.json').read_text())
    assert heldout['passed'] and len(heldout['rows'])==4 and all(r['passed'] and r['checks']==4 for r in heldout['rows'])
    selftest=json.loads((root/'heldout-verifier-selftest.json').read_text())
    assert selftest['evaluator_selftest_only'] and not selftest['model_called'] and selftest['buggy_rejected']==selftest['valid_reference_accepted']==4
    manual = json.loads((root/'answer-review.json').read_text())
    assert manual['all_accepted'] and set(manual['cases']) == set(plan['native_cases'])
    assert all(v['accepted'] and v['response_sha256'] == sha(root/('native-'+n)/'final-response-private.json')
               for n,v in manual['cases'].items())
    incident=json.loads((root/'initial-native-audit-failure.json').read_text())
    assert incident['actual_native_failure']['passed'] is False and incident['actual_native_failure']['client_exit']!=0
    assert incident['actual_native_failure']['read_only_files_unchanged'] and incident['final_error']=='invalid_tool_response'
    stdin_incident=json.loads((root/'stdin-contamination.json').read_text())
    assert stdin_incident['clean_retest_required'] and stdin_incident['effective_request_included_operator_launcher']
    before = json.loads((root/'exploration-before.json').read_text())
    after = json.loads((root/'baseline-after-installed.json').read_text())
    assert len(before['rows']) == len(after['rows']) == 5
    assert all(r['accepted'] for r in before['rows']) and not any(r['accepted'] for r in after['rows'])
    test_summaries = {}
    for name in ('local-tests','linux-tests'):
        p = root/'final-tests'/(name+'.txt')
        counts = {k:int(re.search(r'^# '+k+r' (\d+)$',p.read_text(),re.M).group(1)) for k in ('tests','pass','fail','skipped')}
        assert counts['tests'] == counts['pass'] and counts['fail'] == counts['skipped'] == 0
        test_summaries[name] = {**counts,'log_sha256':sha(p)}
    guards = root/'final-tests/activation-guards.txt'
    assert re.search(r'Ran 9 tests.*?\n\nOK', guards.read_text(), re.S)
    test_summaries['activation_guards'] = {'pass':9,'fail':0,'log_sha256':sha(guards)}
    test_summaries['overlap_do_not_add_mac_linux_counts'] = True
    names = ('receipt-outcome.cjs','verification-scope.cjs','task-continuity.cjs','decision-recovery.cjs','agent-controller.cjs','internal-tool-planner.js')
    hashes = {n:sha(Path('backend/planner')/n) for n in names}
    code = '''import pathlib,sys,json,hashlib,subprocess,urllib.request,datetime
v=json.load(sys.stdin);stage=pathlib.Path(v['stage']);base=pathlib.Path('/opt/harmonika-ai/api')
names=['level18-candidate.json','level18-attestations.json','activation.json','level18-http-canary.json','verification.json','expected-baseline.json']
m={n:json.loads((stage/n).read_text()) for n in names};exam=m['level18-candidate.json'];proof=m['level18-attestations.json']
assert exam['passed'] and exam['passed_count']==exam['total']==12 and len(exam['rows'])==12
assert all(r['passed'] and r['receipt_faults']==1 and r['scope_denials']==0 and r['write_replays']==0 and r['protected_files_unchanged'] for r in exam['rows'])
assert proof['all_verified'] and len(proof['attestations'])==12 and not proof['signing_key_exported']
assert m['activation.json']['status']=='active_pending_postcheck' and m['level18-http-canary.json']['passed']
actual={n:hashlib.sha256((base/n).read_bytes()).hexdigest() for n in v['hashes']}
assert actual==v['hashes']==exam['source_sha256']==m['verification.json']['candidate_sha256']==m['activation.json']['candidate_sha256']
with urllib.request.urlopen('http://127.0.0.1:17890/health',timeout=5) as response:health=response.status
assert health==200
pids={u:int(subprocess.check_output(['systemctl','show','-p','MainPID','--value',u])) for u in v['previous_pids']}
assert all(pids[u]==pid for u,pid in v['previous_pids'].items() if u!='harmonika-ai')
m['release']={'status':'active_verified','verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_sha256':actual,'health_http':health,'service_pids':pids,'unrelated_service_pids_unchanged':True,'native_cli_passed':6,'receipt_recovery_regressions_passed':12}
p=stage/'initiative-release-verification.json';p.write_text(json.dumps(m['release'],indent=2));p.chmod(0o600)
print(json.dumps(m))
'''
    payload = {'stage':stage,'hashes':hashes,'previous_pids':json.loads((root/'pre-activation-state.json').read_text())['service_pids']}
    wrapper='import io,sys,json;v=json.load(sys.stdin);sys.stdin=io.StringIO(json.dumps(v["payload"]));exec(v["code"])'
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','vps','python3 -c '+shlex.quote(wrapper)],
        input=json.dumps({'code':code,'payload':payload}),capture_output=True,text=True,check=True)
    metadata=json.loads(result.stdout)
    private=Path.home()/'.local/state/harmonika-exams/initiative-commands-20261115'
    private.mkdir(mode=0o700,parents=True,exist_ok=True);private.chmod(0o700)
    manifest=[]

    def save(name,data):
        p=private/name;p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');p.chmod(0o600)
        manifest.append({'file':name,'sha256':sha(p)})

    for n,v in metadata.items():save(n if n.endswith('.json') else n+'.json',v)
    save('baseline-before.json',before);save('baseline-after-installed.json',after);save('answer-review.json',manual)
    save('heldout-verification.json',heldout)
    save('heldout-verifier-selftest.json',selftest)
    save('initial-native-audit-failure.json',incident)
    save('stdin-contamination.json',stdin_incident)
    for n in ('local-tests','linux-tests','activation-guards'):
        p=private/(n+'.txt');shutil.copyfile(root/'final-tests'/(n+'.txt'),p);p.chmod(0o600)
        manifest.append({'file':p.name,'sha256':sha(p)})
    development=[]
    for name in ('model-regression.txt','model-regression-final.txt','initial-model-release.txt'):
        p=root/name
        if not p.exists():continue
        rows=[];summary=None
        for line in p.read_text().splitlines():
            try:v=json.loads(line)
            except ValueError:continue
            if v.get('event')=='level18_mission':rows.append({'id':v['id'],'passed':v['passed']})
            if v.get('event')=='level18_summary':summary=v
        development.append({'log_sha256':sha(p),'rows':rows,'summary':summary,
            'used_for_activation':name=='initial-model-release.txt','used_for_final_activation':False,
            'intermediate_activation_pending_postcheck':name=='initial-model-release.txt',
            'purpose':'earlier development candidate before final audit correction; not the final verified release'})
    review={'reviewer':'OpenCode','read_only':True,'claude_involved':False,'original_log_sha256':sha(root/'opencode-review.jsonl'),
        'fixed':['known imperative verbs and mohon prefix','write-prose-then-save classification','shared action classification for claim validation','typed positive-test evidence','explicit repair claims cannot rely on read/pwd alone'],
        'limits_preserved':['topic-first/arbitrary natural word order is not exhaustively classified','zero-receipt blockers may be legitimate and are not forced into unsafe execution','client receipts do not prove semantic truth or permissions']}
    report={'name':plan['name'],'status':metadata['release']['status'],'verified_at':metadata['release']['verified_at'],
        'base_release':'Level18 with initiative-command improvements','not_a_general_intelligence_rank':True,'model_weights_trained':False,
        'roles':{'Codex':'implementation, independent grading, answer review and activation','OpenCode':'read-only counterexample review','Claude':'not involved'},
        'implemented':['Everyday technical inspection commands need actual declared tool actions rather than a promise-only answer.',
            'Known polite/write/save/update verbs preserve scope; questions, prose and plan-only controls remain valid.',
            'Passing-test claims require a cited current positive test receipt rather than generic shell success.',
            'Explicit repair-success wording requires relevant write/test mechanics; scope and fresh verification checks remain active.',
            'Guidance separates observations, hypotheses, missing data and read-only audits; no fabricated settlement from conflicting records.',
            'Legacy read-only audits may answer with actual complete failing-test findings; failure is not converted to a successful repair/test result and cannot bypass operator proof.'],
        'baseline':{'synthetic_checker_only':True,'model_called':False,'tools_executed':False,'unsafe_categories_before':5,'rejected_by_installed_engine':5},
        'tests':test_summaries,'native_command_exam':{'planned':6,'passed':6,'manual_followups':0,'cases':cases,'answer_review':manual,'heldout_verification':heldout},
        'evaluator_selftest_not_model_capability':selftest,
        'same_source_receipt_recovery_regressions':metadata['level18-candidate.json'],
        'operator_proof':{'historical_attestations':12,'issuer_key_exported':False,'not_new_authorization':True},
        'http_postcheck':metadata['level18-http-canary.json'],'activation':{**metadata['activation.json'],**metadata['release']},
        'development_runs':development,'review':review,
        'actual_native_bug_and_retest':{'initial_audit_failed':True,'initial_error':'invalid_tool_response',
            'initial_duration_ms':incident['actual_native_failure']['duration_ms'],'initial_files_unchanged':True,
            'initial_candidate_sha256':incident['installed_candidate_sha256'],
            'correction':'Permit evidenced read-only failing-test findings on legacy answer path; keep successful completion and operator proof gates.',
            'test_assertions_or_audit_acceptance_weakened':False,'retest_passed':next(c for c in cases if c['case']=='audit_read_only')['passed']},
        'harness_incident':{**stdin_incident,'final_six_native_cases_have_clean_stdin':True,
            'preclean_native_cases_used_in_final_score':False,'initial_evidence_directory':(root/'initial-evidence-root.txt').read_text().strip()},
        'scope':{'only_service_restarted':'harmonika-ai','bgp_core_vyos_billing_cs_rtx_modified':False,'real_payment_operations':False,
            'existing_user_sessions_or_provider_settings_changed':False,'tui_binary_or_brand_changed':False,'github_commit_or_push':False},
        'privacy':{'raw_user_chats_or_customer_data_used':False,'raw_synthetic_transcripts_published':False,'logs':'mechanical counts/enums/hashes only','temporary_provider_references_removed':True},
        'limits':['Known intent/claim patterns are not an exhaustive natural-language parser or universal anti-hallucination proof.',
            'Native CLI legacy receipts remain client_reported; operator-signed backend fixtures have separate independent proof.',
            'Keyword grading of synthetic final answers is supplemented by Codex review and cannot prove all future semantic answers.',
            'Read-only hash checks establish final workspace integrity, not a hostile-code sandbox or proof of zero transient writes.',
            'Continuity is task-local RAM/history; no new cross-session database or model training.',
            'Idle/CAS activation is not an atomic external admission fence.'],
        'evidence':{'private_directory':str(private),'manifest':manifest}}
    out=Path('docs/INITIATIVE-COMMAND-EXAM-20261115.json');out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    save(out.name,report)
    print(json.dumps({'status':report['status'],'native_passed':6,'regression_missions_passed':12,'report':str(out.resolve()),'sha256':sha(out)}))


if __name__=='__main__':main()
