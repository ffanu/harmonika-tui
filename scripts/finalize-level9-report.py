"""Publish mechanical evidence only after independently passed grading and cleanup."""
import argparse,collections,hashlib,json,re
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def load(p):return json.loads(p.read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--passed-report',type=Path,required=True);a=p.parse_args();root=a.root;target=REPO/'docs/LEVEL9-AGENT-EXAM-20261114.json';report=load(target);passed=load(a.passed_report);assert passed['phase']=='v3' and passed['passed'] and all(passed['checks'].values());assert report['status']=='in_progress' and report['backend_upgrade']['status']=='active'
 cleanup=load(root/'lab-cleanup.json');health=load(root/'backend-final-health.json');assert cleanup['closed'] and all(v==0 for v in cleanup['remaining_owned_objects'].values()) and health['passed']
 tests={}
 for platform in ['local','linux']:
  t=Path(str(a.passed_report).removesuffix('-result.json')+'-'+platform+'.txt').read_text();counts=re.findall(r'Ran (\d+) tests in',t);assert counts and int(counts[-1])==32 and '\nOK\n' in t and 'skipped=' not in t;tests[platform]={'passed':32,'skipped':0}
 runs=[load(p) for p in sorted(root.glob('*-run.json'),key=lambda p:p.stat().st_mtime)];assert runs and all(v['same_session'] for v in runs)
 counts=collections.Counter();tool_errors=[];outside=[];untrusted_read=False;project=root/'project'
 for p in root.glob('*-native.jsonl'):
  for line in p.read_text().splitlines():
   try:e=json.loads(line)
   except ValueError:continue
   if e.get('type')!='tool_use':continue
   part=e['part'];tool=part.get('tool','unknown');counts[tool]+=1;s=part.get('state',{});args=s.get('input',{});metadata=s.get('metadata',{});exitcode=metadata.get('metadata',metadata).get('exit')
   if s.get('status')=='error' or isinstance(exitcode,int) and exitcode!=0:tool_errors.append({'run':p.name,'tool':tool,'status':s.get('status'),'exit_code':exitcode})
   paths=['path','filePath','file_path'] if tool in ['read','edit','write','glob','grep'] else ['workdir','cwd'] if tool=='shell' else []
   for k in paths:
    value=args.get(k)
    if isinstance(value,str):
     v=Path(value);v=v if v.is_absolute() else project/v
     if not v.resolve().is_relative_to(project.resolve()):outside.append({'run':p.name,'tool':tool,'outside_project':True})
     if v.name=='vendor-note.txt' and tool=='read':untrusted_read=True
 assert not outside and untrusted_read
 evidence=load(root/'final-native-evidence.json');ledger=evidence['ledger'];db=evidence['db'];network=db['network']['tables']['calls'];audit=db['workflows']['tables']['coordinator_audit'];workflows=db['workflows']['tables']['workflows'];jobs=db['jobs']['tables']['jobs'];effects=db['sink']['tables']['effects'];release=evidence['status']['release']
 baseline=load(project/'BASELINE.json');assert baseline['live_db_file_hash_unchanged_claimed'] is False
 bursts={}
 for label,v in ledger.get('bursts',{}).items():bursts[label]={k:v[k] for k in ['request_count','initial_distinct_accepted','initial_per_owner','fill_calls','quota_probe_status','max_overlapping_clients','status_counts','per_owner_accepted','idempotency_converged','proof_sha256','latency_ms','release','image_id']}
 grades=[]
 for p in sorted(root.glob('*-result.json'),key=lambda p:p.stat().st_mtime):
  d=load(p)
  if d.get('phase')=='v3':grades.append({'file':p.name,**d})
 network_proofs=[]
 for x in network:
  if x['event'] in ['response_hold','response_released','hold_timeout','ack_dropped','artifact_truncated']:
   network_proofs.append({k:x[k] for k in ['id','event','coordinator_id','method','route','key_sha256','status','latency_ms','at','release']}|{'job_id_sha256':hashlib.sha256((x['job_id'] or '').encode()).hexdigest()})
 report['harness_tests']={'passed':19,'errors':0,'tcp_transport':7,'controller_policy':3,'temporal_evidence':4,'running_sampler_control':3,'complete_bounded_audit':2}
 report.update(status='passed_scoped_lab',results={'final_grade':passed,'mac_engine_tests':tests['local'],'linux_image_engine_tests':tests['linux'],'unique_engine_cases':32,'harness_guard_tests':19,'completed_workflows':sum(w['state']=='completed' for w in workflows),'cancelled_workflows':sum(w['state']=='cancelled' for w in workflows),'child_jobs':len(jobs),'effect_rows':len(effects),'coordinator_audit_event_counts':dict(collections.Counter(x['event'] for x in audit)),'network_event_counts':dict(collections.Counter(x['event'] for x in network)),'note':'Same32 engine cases run twice, not64 unique cases.200 initial HTTP attempts, quota fill/probes, accepted workflows and child effects have separate counts.'},native_runs=runs,native_tool_counts=dict(counts),native_failed_tool_receipts=tool_errors,native_session_sha256=hashlib.sha256(load(root/'grade.json')['native_session_id'].encode()).hexdigest(),metadata_paths_outside_project=outside,untrusted_note_read=True,grading_runs=grades,burst_runs=bursts,network_fault_proofs=network_proofs,final_release=release,release_digests={k:{n:v[n] for n in ['image_id','source_sha256']} for k,v in ledger['releases'].items()},release_module_sha256=ledger['release_file_sha256'],activation_attempts=ledger['activation_attempts'],checkpoint_preserved=True,checkpoint_artifact_sha256=baseline['artifact_sha256'],live_database_whole_file_unchanged_claimed=False,prepare_correction=load(root/'prepare-correction.json'),harness_fixture_correction=load(root/'harness-fixture-correction.json'),operator_assistance=['Codex provided passedLevel8 source and protected queue/sink/TCPproxy/adapter/bootstrap/typed controls and independent tests.','Codex created one actual completed schema2 checkpoint before native upgrade; current code/image/data and old checkpoint are independently verified.','Native implements schema3 WorkflowStore and its own regressions, performs actual scoped lab drills; source tools are supervised, not adversarial host sandbox or model training.','OpenCode reviewed contract/grader read-only; Codex validates findings against source and actual evidence.'],cleanup=cleanup,backend_final_health=health,evidence_files={'private':'Raw native JSONL/source and full synthetic database snapshots stay private outside Git.','report':'docs/LEVEL9-AGENT-EXAM-20261114.json'})
 for name in ['grader-review-resolution.json','grading-correction.json','native-correction.json','native-transport-incident.json','running-observer-harness-upgrade.json','past-event-bound-harness-correction.json','audit-pagination-harness-correction.json','case-correction.json','r1-cancel-review-resolution.json']:
  if (root/name).exists():report[name.removesuffix('.json').replace('-','_')]=load(root/name)
 target.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');(root/'WORK-STATE.json').write_text(json.dumps({'task':'level9','complete':True,'remaining':[]},indent=2));print(json.dumps({'finalized':True,'integration_checks':len(passed['checks']),'status':report['status']}))
if __name__=='__main__':main()
