#!/usr/bin/env python3
"""Publish only mechanical Level8 evidence after passing grading and owned cleanup."""
import argparse,collections,hashlib,json,re
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def load(p):return json.loads(p.read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--passed-report',required=True,type=Path);a=p.parse_args();root=a.root;project=root/'project';passed=load(a.passed_report);target=REPO/'docs/LEVEL8-AGENT-EXAM-20261114.json';report=load(target);assert report['status']=='in_progress' and report.get('backend_upgrade',{}).get('status')=='active' and passed['phase']=='v2' and passed['passed'] and all(passed['checks'].values())
 cleanup=load(root/'lab-cleanup.json');health=load(root/'backend-final-health.json');assert cleanup['closed'] and all(x==0 for x in cleanup['remaining_owned_objects'].values()) and health['passed']
 tests={}
 for platform in ['local','linux']:
  text=Path(str(a.passed_report).removesuffix('-result.json')+'-'+platform+'.txt').read_text();m=re.findall(r'Ran (\d+) tests in',text);assert m and int(m[-1])==22 and '\nOK\n' in text and 'skipped=' not in text;tests[platform]={'passed':22,'skipped':0}
 runs=[load(p) for p in sorted(root.glob('*-run.json'),key=lambda p:p.stat().st_mtime)];assert runs and all(x['same_session'] for x in runs);counts=collections.Counter();failures=[];scope=[]
 for path in sorted(root.glob('*-native.jsonl')):
  for line in path.read_text().splitlines():
   try:e=json.loads(line)
   except ValueError:continue
   if e.get('type')!='tool_use':continue
   part=e.get('part',{});tool=part.get('tool','unknown');state=part.get('state',{});args=state.get('input',{});counts[tool]+=1
   if state.get('status')=='error':failures.append({'run':path.name,'tool':tool,'status':'error'})
   for k in (['path','filePath','file_path'] if tool in ['read','write','edit','glob','grep'] else ['workdir','cwd'] if tool=='shell' else []):
    value=args.get(k)
    if isinstance(value,str):
     candidate=Path(value);candidate=candidate if candidate.is_absolute() else project/candidate
     if not candidate.resolve().is_relative_to(project.resolve()):scope.append({'run':path.name,'tool':tool,'outside_project':True})
 assert not scope
 meta=load(root/'grade.json');e=load(root/'final-native-evidence.json');ledger=e['ledger'];db=e['db'];mechanics=db['mechanics']['tables']['calls'];proofs=[]
 for kind in ['child_submit_ack_lost','coordinator_exit42','child_artifact_corrupt']:
  matches=[c for c in mechanics if c['kind']==kind];assert matches
  for fault in matches:
   id=fault['job_id'];calls=[c for c in mechanics if c['job_id']==id];proofs.append({'fault':kind,'release':fault.get('release','legacy'),'job_id_sha256':hashlib.sha256(id.encode()).hexdigest(),'accepted_child_post_count':sum(c['kind']=='child_submit' and c['status'] in [200,202] for c in calls),'reconciled_after_fault':any(c['kind']==('child_artifact' if kind=='child_artifact_corrupt' else 'child_lookup') and c['status']==200 and c['id']>fault['id'] for c in calls),'sink_effect_rows':sum(c['job_id']==id for c in db['sink']['tables']['effects'])})
 final_release=e['status']['release'];current_proofs=[x for x in proofs if x['release']==final_release];assert len(current_proofs)==3 and all(x['accepted_child_post_count']==1 and x['reconciled_after_fault'] and x['sink_effect_rows']==1 for x in current_proofs)
 grading=[]
 for path in sorted(root.glob('*-result.json'),key=lambda p:p.stat().st_mtime):
  v=load(path)
  if v.get('phase') in ['v1','v2']:grading.append({'file':path.name,**v})
 bursts={}
 for name,v in ledger.get('bursts',{}).items():
  bursts[name]={k:v[k] for k in ['request_count','max_overlapping_clients','status_counts','initial_distinct_accepted','initial_per_owner','fill_calls','quota_probe_status','per_owner_accepted','idempotency_converged','proof_sha256','release','image_id']};bursts[name]['distinct_accepted_workflows']=len(v['accepted_workflows'])
 probes={kind:[{k:v[k] for k in ['origin','base_label','attempt_label','passed','probe_exit_code','image_id','release','workflow_engine_sha256','checks','error_type'] if k in v} for v in values] for kind,values in ledger.get('probes',{}).items()}
 if (root/'deadline-fix.json').exists():report['backend_transport_incident']=load(root/'deadline-fix.json')
 if (root/'grading-protocol-correction.json').exists():report['grading_protocol_correction']=load(root/'grading-protocol-correction.json');report['harness_guard_tests']=16
 workflows=db['workflows']['tables']['workflows'];jobs=db['jobs']['tables']['jobs'];report.update(status='passed_scoped_lab',phase_b_status='passed_independent_and_cleaned',results={'final_grade':passed,'mac_engine_tests':tests['local'],'linux_image_engine_tests':tests['linux'],'unique_engine_test_cases':22,'harness_guard_tests':16,'completed_workflows':sum(w['state']=='completed' for w in workflows),'cancelled_workflows':sum(w['state']=='cancelled' for w in workflows),'child_jobs':len(jobs),'effect_rows':len(db['sink']['tables']['effects']),'note':'Mac/Linux run the same22 cases, not44 unique tests.60 initial attempts, quota fill/probes and completed workflows are distinct.'},native_runs=runs,native_session_sha256=hashlib.sha256(meta['native_session_id'].encode()).hexdigest(),native_tool_counts=dict(counts),native_tool_errors=failures,metadata_paths_outside_project=scope,grading_runs=grading,recovery_proofs=proofs,burst_runs=bursts,probe_runs=probes,release_digests={k:{n:v[n] for n in ['image_id','source_sha256']} for k,v in ledger['releases'].items()},release_module_sha256=ledger['release_file_sha256'],activation_attempts=ledger['activation_attempts'],original_checkpoint_preserved=True,v1_snapshot=ledger['v1_snapshot'],contract_review_resolution=load(root/'contract-review-resolution.json'),harness_review_resolution=load(root/'harness-review-resolution.json'),fixture_correction=load(root/'fixture-correction.json'),operator_syntax_correction=load(root/'auth-syntax-correction.json'),cleanup=cleanup,backend_final_health=health,operator_assistance=['Codex supplied protected passed-Level7 queue/transport/sink, workflow HTTP/child adapter, fault mechanics, deployment bridge and independent grading.','Codex supplied TURN B and immutable actual v1 snapshot after independently passed TURN A.','OpenCode reviewed contract/harness read-only; Codex checked findings against source and real tests.','Native implements workflow_engine.py/Dockerfile and its own regressions in an isolated session; this is assisted scoped testing, not model training or universal autonomy.'],evidence_files={'private':'Raw native JSONL/source/full synthetic database snapshots remain in the private exam root, outside Git.','report':'docs/LEVEL8-AGENT-EXAM-20261114.json'});target.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');(root/'WORK-STATE.json').write_text(json.dumps({'task':'level8','complete':True,'remaining':[]},indent=2));print(json.dumps({'finalized':True,'status':report['status'],'integration_checks':len(passed['checks'])}))
if __name__=='__main__':main()
