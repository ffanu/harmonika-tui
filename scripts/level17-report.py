#!/usr/bin/env python3
"""Seal mechanical exam evidence after real deployment and native CLI checks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root
    metadata = json.loads((root/'remote-metadata.json').read_text())
    native = metadata['level17-candidate.json']
    cases = [json.loads((root/('native-'+name)/'initiative-native-result.json').read_text())
             for name in ('arithmetic', 'chained', 'tool_fallback')]
    assert len(native['rows']) == 12 and native['passed_count'] == 12 and native['passed'] is True
    assert all(c['passed'] is True and c['client_exit'] == c['independent_test_exit'] == 0
               and c['expected_tests_passed'] and c['protected_files_unchanged'] and c['manual_followups'] == 0
               and c['temporary_provider_reference_removed'] for c in cases)
    assert all(row['scope_faults'] == 1 for row in native['rows'])
    assert metadata['level17-attestations.json']['all_verified']
    assert len(metadata['level17-attestations.json']['attestations']) == 12
    assert metadata['level17-http-canary.json']['passed']
    hashes = metadata['verification.json']['candidate_sha256']
    assert hashes == native['source_sha256']
    assert all(sha(Path('backend/planner')/n) == value for n, value in hashes.items())
    code = '''import sys,json,pathlib,hashlib,subprocess,urllib.request,datetime
v=json.load(sys.stdin);p=pathlib.Path('/opt/harmonika-ai/api')
actual={n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in v['hashes']};assert actual==v['hashes']
with urllib.request.urlopen('http://127.0.0.1:17890/health',timeout=5) as response:health=response.status
assert health==200
units=['harmonika-ai','harmonika-web-chat-live','harmonika-shared-core','harmonika-coding-runtime']
pids={u:int(subprocess.check_output(['systemctl','show','-p','MainPID','--value',u]).strip()) for u in units}
assert all(pids[u]==pid for u,pid in v['previous_pids'].items() if u!='harmonika-ai')
result={'status':'active_verified','verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_sha256':actual,'health_http':health,'service_pids':pids,'backend_passed':12,'native_cli_passed':3,'native_cli':v['cases'],'unrelated_service_pids_unchanged':True}
out=pathlib.Path(v['stage'])/'level17-release-verification.json';out.write_text(json.dumps(result,indent=2));out.chmod(0o600)
print(json.dumps(result))
'''
    payload = {'hashes': hashes, 'cases': cases, 'stage': (root/'stage.txt').read_text().strip(),
               'previous_pids': json.loads((root/'pre-activation-state.json').read_text())['service_pids']}
    # Script and payload go over stdin, never shell interpolation or credential argv.
    remote = 'import io,sys,json;v=json.load(sys.stdin);sys.stdin=io.StringIO(json.dumps(v["payload"]));exec(v["code"])'
    import shlex
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'vps',
                             'python3 -c '+shlex.quote(remote)],
                            input=json.dumps({'code': code, 'payload': payload}), capture_output=True, text=True, check=True)
    release = json.loads(result.stdout)
    private = Path.home()/'.local/state/harmonika-exams/level17-20261115'
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.chmod(0o700)
    manifest = []

    def save(name, data):
        p = private/name
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
        p.chmod(0o600)
        manifest.append({'file': name, 'sha256': sha(p)})

    for name, data in metadata.items():
        save(name+'.json' if name == 'installed_state' else name, data)
    save('level17-release-verification.json', release)
    before = json.loads((root/'baseline-before.json').read_text())
    after = json.loads((root/'baseline-after-installed.json').read_text())
    assert sum(r['accepted'] for r in before['rows']) == 9
    assert len(after['rows']) == 9 and not any(r['accepted'] for r in after['rows'])
    save('baseline-before.json', before)
    save('baseline-after-installed.json', after)
    tests = {}
    for name in ('local-tests', 'linux-tests'):
        p = root/'release-tests'/(name+'.txt')
        text = p.read_text()
        counts = {k: int(re.search(r'^# '+k+r' (\d+)$', text, re.M).group(1))
                  for k in ('tests', 'pass', 'fail', 'skipped')}
        assert counts['tests'] == counts['pass'] and counts['fail'] == counts['skipped'] == 0
        tests[name] = {**counts, 'original_log_sha256': sha(p)}
    p = root/'release-tests/activation-guards.txt'
    assert re.search(r'Ran 9 tests.*?\n\nOK', p.read_text(), re.S)
    tests['activation_guards'] = {'tests': 9, 'pass': 9, 'fail': 0, 'original_log_sha256': sha(p)}
    tests['mac_linux_counts_overlap_do_not_add'] = True
    save('test-summaries.json', tests)
    review = {'reviewer': 'OpenCode', 'read_only': True, 'claude_involved': False,
              'original_review_sha256': sha(root/'opencode-review.jsonl'),
              'findings_addressed': ['cross-project cwd test cannot verify current writes', 'pinned historical readback and content mismatch', 'foreign owner/project job settlement', 'relative path collision across projects']}
    save('review-dispositions.json', review)
    invalid = json.loads((root/'invalid-plan-attempt.json').read_text())
    guard = json.loads((root/'plan-guard-rejection.json').read_text())
    save('invalid-plan-attempt.json', invalid)
    save('plan-guard-rejection.json', guard)
    scoped_failure = json.loads((root/'scoped-failed-attempt.json').read_text())
    save('scoped-failed-attempt.json', scoped_failure)
    replay = json.loads((root/'recorded-response-regression.json').read_text())
    save('recorded-response-regression.json', replay)
    report = {
        'level': 17, 'name': 'Scope target, proyek, revisi dan attempt pada bukti verifikasi',
        'status': release['status'], 'verified_at': release['verified_at'],
        'meaning': 'Internal bounded acceptance suite, not a general intelligence rank or model training.',
        'roles': {'Codex': 'implementation, grading, testing and activation', 'OpenCode': 'read-only review', 'Claude': 'not involved'},
        'implemented': ['Explicit contradictory owner/project/workspace/target/attempt/revision cannot verify a write.',
                        'Static test cwd must lexically contain the edited target; sibling prefixes and traversal do not match.',
                        'Separate project obligations for identical relative paths; newer revisions of the same normalized target supersede older writes.',
                        'Matching job ID under foreign explicit scope cannot resolve unknown operations; pending scope enrichment is preserved.',
                        'Pinned historical read/ref and mismatching full-file readback do not establish current write verification.',
                        'Cline clipped queries, POSIX/Windows lexical paths and unknown legacy scope retain documented compatibility.',
                        'Test entry paths are separate from executor target IDs; explicit workspace revisions advance across files, file revisions remain per target.'],
        'baseline': {'synthetic_checker_only': True, 'live_model_called': False,
                     'before_unsafe_completion_categories_accepted': 9, 'after_installed_engine_rejected': 9},
        'tests': tests, 'backend_exam': native,
        'operator_proof': {'historical_attestations_verified': 12, 'source_workspace_revisions_match': True,
                           'issuer_key_exported': False, 'not_new_authorization': True},
        'native_cline_regressions': {'planned': 3, 'passed': 3, 'results': cases,
                                     'existing_user_sessions_or_settings_changed': False},
        'http_postcheck': metadata['level17-http-canary.json'],
        'activation': {**metadata['activation.json'], **{k: v for k, v in release.items() if k != 'native_cli'}},
        'scope': {'only_service_restarted': 'harmonika-ai', 'bgp_core_vyos_billing_cs_rtx_modified': False,
                  'tui_brand_or_binary_changed': False, 'github_commit_or_push': False,
                  'live_customer_history_replayed': False, 'tests_use_isolated_synthetic_workspaces': True},
        'exam_provenance': ['S01-S06 and R01-R04 run actual protected Node tests and a separate bounded function verifier. One passing execution response has deliberately altered scope metadata; no independent operator proof is issued for it. A new matching check is required.',
                            'R05 begins with one actual operator-seeded SQLite commit and lost acknowledgement. The first terminal lookup has altered owner metadata; model reconciles the original operation again without a duplicate submit.',
                            'R06 begins with one actual operator-seeded cancellation of an owned worker. First physical-exit response has altered owner metadata and cannot settle the original scope; a matching fresh check is required.',
                            'Cross-cwd, relative collisions, pinned refs, content hashes and Windows normalization are synthetic checker regressions, not a physical Windows terminal exam. Native Cline regressions are three separate real client runs.'],
        'exam_incidents': {'initial_invalid_plan': {'executed_missions': len(invalid['candidate']['rows']), 'loaded_plan_level': invalid['plan']['level'],
                                                    'not_a_valid_level17_capability_grade': True, 'used_for_activation': False,
                                                    'correction': 'Fix uppercase plan filename; validate level, mission IDs and scope faults before any model dispatch.',
                                                    'new_guard_rejection': guard},
                           'first_valid_scoped_run': {'passed': scoped_failure['passed_count'], 'total': scoped_failure['total'], 'used_for_activation': False, 'engine_corrections': ['Separate test target entry path from executor target ID.', 'Advance explicit workspace revisions across edited files without treating file revisions as workspace revisions.']},
                           'development_regression': {'case': 'UNC parent normalization', 'initial': '26/27 focused tests passed', 'fixed': True, 'provenance': 'initial terminal tool output; no regenerated historical failure log'}},
        'unchanged_staging_dependency_sha256': metadata['stage-dependencies.json'],
        'review': review,
        'recorded_response_regression': replay,
        'privacy': {'evidence': 'mechanical enums, counts and hashes only; no raw user chat, customer data or credentials',
                    'raw_synthetic_transcripts_published': False, 'temporary_native_provider_references_removed': True},
        'limits': ['Legacy tool scopes and receipts remain client_reported; matching client fields cannot issue independent attestation or tenant authorization.',
                   'Unknown legacy scope keeps compatibility. Scope is not inferred from arbitrary prose and absent metadata cannot establish independently verified target coverage.',
                   'Path matching is lexical without filesystem/symlink resolution, semantic coverage proof or a hostile-code sandbox; Windows tests are not a physical Windows exam.',
                   'Only static known cd/cwd forms are supported. Ambiguous roots or workspace selection need a clearer receipt.',
                   'Continuity remains current-task history in RAM, not new durable cross-session storage or model training.',
                   'Activation uses idle checks and CAS backups; it is not an atomic external admission fence.',
                   'Passing these bounded fixtures is not a universal intelligence certification or guarantee for every future task.'],
        'evidence': {'private_directory': str(private), 'manifest': manifest},
    }
    output = Path('docs/LEVEL17-AGENT-EXAM-20261115.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    save(output.name, report)
    print(json.dumps({'status': report['status'], 'report': str(output.resolve()), 'sha256': sha(output)}))


if __name__ == '__main__':
    main()
