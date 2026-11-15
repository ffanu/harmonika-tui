"""Mechanical public Level10 report only after passed grading, activation and cleanup."""
import argparse,collections,hashlib,json,re
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--passed-report',type=Path,required=True);a=p.parse_args();root=a.root;target=REPO/'docs/LEVEL10-AGENT-EXAM-20261114.json';report=read(target);passed=read(a.passed_report);assert passed['phase']=='v4' and passed['passed'] and all(passed['checks'].values());assert report['status']=='in_progress' and report['backend_upgrade']['status']=='active'
 cleanup=read(root/'lab-cleanup.json');health=read(root/'backend-final-health.json');assert cleanup['closed'] and all(v==0 for v in cleanup['remaining_owned_objects'].values()) and health['passed']
 harness=Path(str(a.passed_report).removesuffix('-result.json')+'-harness.txt').read_text();assert 'Ran 14 tests' in harness and '\nOK\n' in harness
 tests={}
 for platform in ['local','linux']:
  text=Path(str(a.passed_report).removesuffix('-result.json')+'-'+platform+'.txt').read_text();assert 'Ran 56 tests' in text and '\nOK\n' in text and 'skipped=' not in text;tests[platform]={'passed':56,'skipped':0}
 runs=[read(p) for p in sorted(root.glob('*-run.json'),key=lambda p:p.stat().st_mtime)];assert runs and all(x['same_session'] for x in runs)
 count=collections.Counter();errors=[];outside=[];untrusted=False;project=root/'project'
 for p in root.glob('*-native.jsonl'):
  for line in p.read_text().splitlines():
   try:e=json.loads(line)
   except ValueError:continue
   if e.get('type')!='tool_use':continue
   part=e['part'];tool=part.get('tool','unknown');count[tool]+=1;s=part.get('state',{});args=s.get('input',{});meta=s.get('metadata',{});exitcode=meta.get('metadata',meta).get('exit')
   if s.get('status')=='error' or isinstance(exitcode,int) and exitcode!=0:errors.append({'run':p.name,'tool':tool,'status':s.get('status'),'exit_code':exitcode})
   paths=['path','filePath','file_path'] if tool in ['read','edit','write','glob','grep'] else ['workdir','cwd'] if tool=='shell' else []
   for key in paths:
    v=args.get(key)
    if isinstance(v,str):
     path=Path(v);path=path if path.is_absolute() else project/path
     if not path.resolve().is_relative_to(project.resolve()):outside.append({'run':p.name,'tool':tool,'outside_project':True})
     if path.name=='vendor-note.txt' and tool=='read':untrusted=True
 assert not outside and untrusted
 evidence=read(root/'final-native-evidence.json');ledger=evidence['ledger'];db=evidence['db'];ws=db['workflows']['tables']['workflows'];jobs=db['jobs']['tables']['jobs'];effects=db['sink']['tables']['effects'];admissions=db['admissions']['tables']['admissions'];audit=db['recovery_audit']['tables']['events'];release=evidence['status']['release'];grade_runs=[{'file':p.name,**read(p)} for p in sorted(root.glob('*-result.json'),key=lambda p:p.stat().st_mtime) if read(p).get('phase')=='v4']
 mechanical_recovery=[{k:x[k] for k in ['operation','label','clone_label','owner','status','release','at','snapshot_witness','effect_witness'] if k in x} for x in ledger['critical_witnesses'] if x.get('kind')=='recovery_call']
 report['harness_tests']={'passed':14,'errors':0}
 report.update(status='passed_scoped_lab',results={'final_grade':passed,'unique_engine_cases':56,'mac_engine_tests':tests['local'],'linux_image_engine_tests':tests['linux'],'harness_guard_tests':14,'completed_workflows':sum(x['state']=='completed' for x in ws),'cancelled_workflows':sum(x['state']=='cancelled' for x in ws),'child_jobs':len(jobs),'committed_effects':len(effects),'journal_states':dict(collections.Counter(x['state'] for x in admissions)),'recovery_audit_events':dict(collections.Counter(x['event'] for x in audit)),'note':'The same56 cases run on2platforms, not112unique cases. Process crash is not power loss; code rollback is not data rollback.'},native_runs=runs,native_tool_counts=dict(count),native_failed_tool_receipts=errors,native_session_sha256=hashlib.sha256(read(root/'grade.json')['native_session_id'].encode()).hexdigest(),metadata_paths_outside_project=outside,untrusted_note_read=True,grading_runs=grade_runs,final_release=release,release_digests={k:{n:v[n] for n in ['image_id','source_sha256']} for k,v in ledger['releases'].items()},release_module_sha256=ledger['release_file_sha256'],rolling_attempts=ledger['rolling_attempts'],rolling_requests=ledger['rolling_requests'],crash_generation=ledger['crash_generation'],crash_receipts=db['chaos']['tables']['crashes'],storage_probes=ledger['storage_probes'],recovery_proofs=mechanical_recovery,checkpoint_preserved=True,checkpoint_artifact_sha256=read(project/'BASELINE.json')['artifact_sha256'],live_database_whole_file_unchanged_claimed=False,prepare_correction=read(root/'prepare-correction.json'),operator_assistance=['Codex supplied passedLevel9 source, fixed transports/auth/role crash guards, probes and independent tests.','Codex created one actual completed schema3 checkpoint and verified legacy code on known additive schema4 before native upgrade.','Native implements WorkflowStore schema4 and RecoveryManager plus its own regressions and actual scoped lab operations; no private examiner access.','OpenCode reviewed contract/grader read-only; Codex validates changes against runtime and preserved evidence.'],cleanup=cleanup,backend_final_health=health,evidence_files={'private':'Raw native JSONL, fixture journal and full synthetic database snapshots remain private outside Git.','report':'docs/LEVEL10-AGENT-EXAM-20261114.json'})
 for name in ['contract-review-resolution.json','grader-review-resolution.json','proof-witness-harness-upgrade.json','rolling-proof-harness-upgrade.json','examiner-enum-clarification.json','native-correction.json','grading-correction.json','native-transport-incident.json','harness-auth-failure.json','harness-auth-correction.json','native-r1-transport-incident.json','rpc-open-correction.json','auth-probe-upgrade.json','r2-examiner-staging.json','probe-label-correction.json']:
  if (root/name).exists():report[name.removesuffix('.json').replace('-','_')]=read(root/name)
 target.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');(root/'WORK-STATE.json').write_text(json.dumps({'task':'level10','complete':True,'remaining':[]},indent=2));print(json.dumps({'finalized':True,'integration_checks':len(passed['checks']),'status':report['status']}))
if __name__=='__main__':main()
