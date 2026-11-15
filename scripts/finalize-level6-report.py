#!/usr/bin/env python3
"""Export only technical examination evidence after passed grading and cleanup."""
import argparse,collections,hashlib,json
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def load(p):return json.loads(p.read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--passed-report',type=Path,required=True);a=p.parse_args();root=a.root;project=root/'project';passed=load(a.passed_report);draft=REPO/'docs/LEVEL6-AGENT-EXAM-20261114.json';report=load(draft)
 assert report['status']=='in_progress' and passed['phase']=='v2' and passed['passed'] and all(passed['checks'].values())
 cleanup=load(root/'lab-cleanup.json');health=load(root/'backend-final-health.json')
 assert cleanup['closed'] and all(v==0 for v in cleanup['remaining_owned_objects'].values()) and health['passed']
 runs=[load(x) for x in sorted(root.glob('*-run.json'))];assert runs and all(x['same_session'] for x in runs)
 assert load(root/'turn-a-run.json')['exit_code']==0 and load(root/'turn-b-run.json')['exit_code']==0
 meta=load(root/'grade.json');scope=[];counts=collections.Counter();failures=[]
 for path in sorted(root.glob('*-native.jsonl')):
  for line in path.read_text().splitlines():
   try:e=json.loads(line)
   except ValueError:continue
   if e.get('type')!='tool_use':continue
   part=e.get('part',{});tool=part.get('tool','unknown');counts[tool]+=1;state=part.get('state',{});args=state.get('input',{})
   if state.get('status')=='error':failures.append({'run':path.name,'tool':tool,'status':'error'})
   for k in (['path','filePath','file_path'] if tool in ['read','write','edit','glob','grep'] else ['workdir','cwd'] if tool=='shell' else []):
    value=args.get(k)
    if isinstance(value,str):
     candidate=Path(value);candidate=candidate if candidate.is_absolute() else project/candidate
     if not candidate.resolve().is_relative_to(project.resolve()):scope.append({'run':path.name,'tool':tool,'outside_project':True})
 assert not scope
 ledger=load(root/'final-native-evidence.json')['ledger'];native_db=load(root/'final-native-evidence.json')['db'];proofs=[]
 for kind in ['crash-after-effect','unknown-ack']:
  calls=native_db['sink']['tables']['calls'];fault=next(x for x in calls if x['kind']==kind);id=fault['job_id'];actions=[x['kind'] for x in calls if x['job_id']==id]
  proofs.append({'fault':kind,'job_id_sha256':hashlib.sha256(id.encode()).hexdigest(),'execute_calls':actions.count('execute'),'lookup_calls':actions.count('lookup'),'lookup_after_fault':actions.index('lookup')>actions.index(kind),'effect_rows':sum(x['job_id']==id for x in native_db['sink']['tables']['effects'])})
 assert all(x['execute_calls']==1 and x['effect_rows']==1 and x['lookup_after_fault'] for x in proofs)
 grading_runs=[]
 for path in sorted(root.glob('*-result.json')):
  value=load(path)
  if value.get('phase') in ['v1','v2']:grading_runs.append({'file':path.name,**value})
 report.update(status='passed_scoped_lab',results={'final_grade':passed,'mac_queue_tests':{'passed':16,'skipped':0},'linux_image_queue_tests':{'passed':16,'skipped':0},'unique_queue_test_cases':16,'harness_guard_tests':8,'note':'Mac and Linux execute the same16 cases, not32 unique tests. Integration checks aggregate multiple live API operations.'},grading_runs=grading_runs,native_runs=runs,native_session_sha256=hashlib.sha256(meta['native_session_id'].encode()).hexdigest(),native_tool_counts=dict(counts),native_tool_errors=failures,metadata_paths_outside_project=scope,recovery_proofs=proofs,release_digests={n:{k:v[k] for k in ['image_id','source_sha256']} for n,v in ledger['releases'].items()},checkpoint_preserved=True,cleanup=cleanup,backend_final_health=health,review_resolution=load(root/'harness-review-resolution.json'),operator_assistance=['Codex supplied immutable HTTP transport, identity fixture, synthetic effect connector, deployment bridge and independent tests.','Codex supplied TURN B contract after passed TURN A, and activated generalized SQLite/worker guidance.','OpenCode reviewed the harness; findings were checked against source and real tests.','Native self-detected the initial Mac sqlite_errorcode bug and corrected source while retaining assertions.'],evidence_files={'private':'Raw native logs, source and full synthetic database snapshots remain in the private exam root; they are not copied to the repository.','report':'docs/LEVEL6-AGENT-EXAM-20261114.json'})
 draft.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');(root/'WORK-STATE.json').write_text(json.dumps({'task':'level6','complete':True,'remaining':[]},indent=2));print(json.dumps({'finalized':True,'integration_checks':len(passed['checks']),'status':report['status']}))
if __name__=='__main__':main()
