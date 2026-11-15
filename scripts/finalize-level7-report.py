#!/usr/bin/env python3
"""Export mechanical evidence only after independent grading and owned cleanup."""
import argparse,collections,hashlib,json,re
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def load(path):return json.loads(path.read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--passed-report',required=True,type=Path);a=p.parse_args();root=a.root;project=root/'project';passed=load(a.passed_report);draft=REPO/'docs/LEVEL7-AGENT-EXAM-20261114.json';report=load(draft)
 assert report['status']=='in_progress' and passed['phase']=='v2' and passed['passed'] and all(passed['checks'].values())
 test_counts={}
 for platform in ['local','linux']:
  transcript=Path(str(a.passed_report).removesuffix('-result.json')+'-'+platform+'.txt').read_text();matches=re.findall(r'Ran (\d+) tests in',transcript);assert matches and int(matches[-1])==21 and '\nOK\n' in transcript and 'skipped=' not in transcript;test_counts[platform]={'passed':21,'skipped':0}
 cleanup=load(root/'lab-cleanup.json');health=load(root/'backend-final-health.json');assert cleanup['closed'] and all(v==0 for v in cleanup['remaining_owned_objects'].values()) and health['passed']
 runs=[load(x) for x in sorted(root.glob('*-run.json'))];assert runs and all(x['same_session'] for x in runs)
 assert load(root/'turn-a-backlog-run.json')['exit_code']==0
 meta=load(root/'grade.json');scope=[];counts=collections.Counter();failures=[]
 for path in sorted(root.glob('*-native.jsonl')):
  for line in path.read_text().splitlines():
   try:e=json.loads(line)
   except ValueError:continue
   if e.get('type')!='tool_use':continue
   part=e.get('part',{});tool=part.get('tool','unknown');counts[tool]+=1;state=part.get('state',{});args=state.get('input',{})
   if state.get('status')=='error':failures.append({'run':path.name,'tool':tool,'status':'error'})
   keys=['path','filePath','file_path'] if tool in ['read','write','edit','glob','grep'] else ['workdir','cwd'] if tool=='shell' else []
   for k in keys:
    value=args.get(k)
    if isinstance(value,str):
     candidate=Path(value);candidate=candidate if candidate.is_absolute() else project/candidate
     if not candidate.resolve().is_relative_to(project.resolve()):scope.append({'run':path.name,'tool':tool,'outside_project':True})
 assert not scope
 evidence=load(root/'final-native-evidence.json');ledger=evidence['ledger'];db=evidence['db'];calls=db['sink']['tables']['calls'];effects=db['sink']['tables']['effects'];proofs=[]
 for kind in ['crash-after-effect','unknown-ack','bad-receipt']:
  matching=[x for x in calls if x['kind']==kind];assert len(matching)==1;fault=matching[0];id=fault['job_id'];actions=[x for x in calls if x['job_id']==id]
  proofs.append({'fault':kind,'job_id_sha256':hashlib.sha256(id.encode()).hexdigest(),'execute_calls':sum(x['kind']=='execute' for x in actions),'lookup_calls':sum(x['kind']=='lookup' for x in actions),'lookup_after_fault':any(x['kind']=='lookup' and x['id']>fault['id'] for x in actions),'effect_rows':sum(x['job_id']==id for x in effects)})
 assert all(x['execute_calls']==1 and x['effect_rows']==1 and x['lookup_after_fault'] for x in proofs)
 grading=[]
 for path in sorted(root.glob('*-result.json')):
  value=load(path)
  if value.get('phase') in ['v1','v2']:grading.append({'file':path.name,**value})
 bursts={}
 for name,v in ledger['bursts'].items():
  bursts[name]={k:v[k] for k in ['request_count','max_overlapping_clients','unique_request_keys','status_counts','transport_error_counts','idempotency_converged','per_owner_pending','observed_worker_ids','proof_sha256','release','image_id'] if k in v};bursts[name]['distinct_accepted_jobs']=len(v['accepted_jobs'])
 probes={kind:[{k:v[k] for k in ['origin','passed','image_id','stream_reader_sha256','storage_tools_sha256','probe_exit_code','label','base_label','attempt_label','error_type','checks'] if k in v} for v in values] for kind,values in ledger.get('probes',{}).items()}
 report.update(status='passed_scoped_lab',phase_b_status='passed_independent_and_cleaned',results={'final_grade':passed,'mac_engine_tests':test_counts['local'],'linux_image_engine_tests':test_counts['linux'],'unique_engine_test_cases':21,'completed_jobs':len(db['jobs']['tables']['jobs']),'effect_rows':len(effects),'harness_guard_tests':19,'note':'Mac and Linux run the same21 cases, not42 unique tests.100 HTTP requests are not100 completed jobs.'},grading_runs=grading,native_runs=runs,native_session_sha256=hashlib.sha256(meta['native_session_id'].encode()).hexdigest(),native_tool_counts=dict(counts),native_tool_errors=failures,metadata_paths_outside_project=scope,recovery_proofs=proofs,burst_runs=bursts,probe_runs=probes,release_digests={n:{k:v[k] for k in ['image_id','source_sha256']} for n,v in ledger['releases'].items()},release_module_sha256=ledger['release_file_sha256'],activation_attempts=ledger['activation_attempts'],checkpoint_preserved=True,docker_event_archive_provenance=ledger.get('docker_event_archive_provenance',[]),harness_correction=load(root/'harness-correction.json'),cleanup=cleanup,backend_final_health=health,review_resolution=load(root/'harness-review-resolution.json'),operator_assistance=['Codex supplied protected HTTP transport, fixed identity fixtures, fenced synthetic effect service, bounded deployment bridge and independent tests.','Codex supplied TURN B after passed corrected TURN A, reviewed failures, and activated the generalized planner timeout/admission fix.','The initial native decision timed out before any client tool execution; source/session were preserved. Later same-session turns used the corrected planner.','Initial100-client test failed36 connections. Operator calibrated protected Python HTTP backlog5 to256, preserved failed proof and immutable old image, and required a new image and full unchanged retest.','OpenCode performed read-only contract/harness review; Codex validated findings against source and actual tests.','Independent default-event malformed-frame checks failed in both runtimes. Codex clarified the original underspecified default/message event detail; Harmonika added regressions and corrected the parser in immutable v2-r1. Valid default/message acceptance was additionally tested; no original assertion was removed.','Docker daemon event retention lost the earlier exit42 after further deployments. Codex recovered the exact previously observed protected native controller output by SHA256, matched registered container/role/release identities and restart count, and added a durable event archive with provenance. No crash was replayed or event invented.'],evidence_files={'private':'Raw native JSONL, implementation source, full synthetic database snapshots and fault/probe evidence remain in a private exam root, outside Git.','report':'docs/LEVEL7-AGENT-EXAM-20261114.json'})
 draft.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');(root/'WORK-STATE.json').write_text(json.dumps({'task':'level7','complete':True,'remaining':[]},indent=2));print(json.dumps({'finalized':True,'integration_checks':len(passed['checks']),'status':report['status']}))
if __name__=='__main__':main()
