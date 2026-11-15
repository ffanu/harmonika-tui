"""Verify native recovery receipts against private operator results and live state."""
import fcntl, hashlib, importlib.util, json, time
from pathlib import Path

REPO=Path(__file__).resolve().parents[1];R=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());P=R/'project'
lock=(R/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
meta=json.loads((R/'project-meta.json').read_text())
assert all(hashlib.sha256((P/n).read_bytes()).hexdigest()==sha for n,sha in meta['protected'].items())
def load(n):return json.loads((P/'reports'/n).read_text())
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
ops=[json.loads(line) for line in (R/'operations.jsonl').read_text().splitlines()]
observed={digest(row['result']) for row in ops if row.get('ok')}
checks=[]
def check(value,name):
    checks.append({'name':name,'passed':bool(value)})
    if not value:raise AssertionError(name)
def provenance(value):
    if isinstance(value,dict):
        if '_source_binding' in value or {'status','body','headers'}<=set(value):
            check(digest(value) in observed,'receipt_matches_actual_operator_result')
        for v in value.values():provenance(v)
    elif isinstance(value,list):
        for v in value:provenance(v)
docs={n:load(n) for n in ['turn-c-lock.json','turn-c-backup.json','turn-c-bad-config.json','turn-c-code-rollback.json','turn-c-old-code-data.json','turn-c-resume-v2.json']}
for doc in docs.values():provenance(doc)
v2=json.loads((R/'releases/v2/manifest.json').read_text());sha=v2['source_sha256']
d=docs['turn-c-lock.json']
check(d['lock_start']['active'] and d['health_locked']['status']==200 and d['ready_locked']['status']==503 and d['create_locked']['status']==503,'real_writer_lock_liveness_and_write_readiness')
check(d['create_locked']['body']=={'error':'sqlite_busy'} and d['same_key_retry']['status']==201 and d['same_key_replay']['status']==200 and d['same_key_retry']['body']['customer']['id']==d['same_key_replay']['body']['customer']['id'] and d['same_key_replay']['body']['replayed'],'same_identity_recovers_without_duplicate')
d=docs['turn-c-backup.json'];b=d['backup'];c=d['inactive_clone'];n=b['data_invariants']['counts']['customers']
check(b['closed_snapshot'] and b['integrity']=='ok' and b['writer_state']['active'] and d['writer_before']['active'] and d['writer_after']['active'] and d['writer_after']['ticks']>d['writer_before']['ticks'] and d['elapsed_seconds']<30,'closed_online_snapshot_during_advancing_writer')
check(c['sha256']==b['sha256'] and c['matches_backup'] and c['snapshot_data_match'] and c['integrity']=='ok' and c['live_database_unchanged'] and c['counts']['customers']==n and c['live_after']['counts']['customers']==n+1,'inactive_clone_matches_snapshot_without_live_rollback')
d=docs['turn-c-code-rollback.json'];old=d['after'];deployment=d['rollback_deploy']
check(deployment['operation']=='rollback-to-v1' and deployment['state']=='completed' and not deployment.get('historic_receipt') and old['current']['source_sha256']==meta['baseline_source_sha256'] and old['schema']['version']==2 and old['pid_bound_ready'] and old['app']['MainPID']!=d['before']['app']['MainPID'],'actual_code_rollback_on_current_schema')
d=docs['turn-c-old-code-data.json']
check(d['common_patch']['status']==200 and d['segment_unsupported']['status']==422 and d['post_backup_customer']['status']==200,'old_code_common_write_and_additive_column_boundary')
d=docs['turn-c-resume-v2.json']
check(d['resume_deploy']['operation']=='resume-v2' and d['resume_deploy']['state']=='completed' and not d['resume_deploy'].get('historic_receipt') and d['after']['current']['source_sha256']==sha and d['baseline']['body']['customer']['segment']==load('turn-c-v2-verification.json')['baseline_replay']['body']['customer']['segment'] and d['same_key_retry']['body']['customer']['segment']=='vip' and d['post_backup_customer']['body']['customer']['segment']=='vip' and d['cached_session']['status']==200,'same_immutable_v2_resumed_with_vip_and_session')
check(d['baseline']['body']['customer']['name']==docs['turn-c-old-code-data.json']['common_patch']['body']['customer']['name'],'common_field_edit_survives_code_resume')
d=docs['turn-c-bad-config.json']
check(d['bad_config_start']['failed_as_expected'] and not d['stopped_status']['pid_bound_ready'] and d['recovered_status']['pid_bound_ready'] and d['/healthz']['status']==200 and d['/readyz']['status']==200 and d['/api/session']['status']==200,'bad_configuration_then_explicit_recovery')
followup=json.loads((R/'WORK-STATE.json').read_text()).get('recovery_followup_label','c1');assert followup in ['c1','c2','c3'];run=json.loads((R/(followup+'-run.json')).read_text());check(run['exit_code']==0 and run['same_session'],'native_followup_same_session_completed')
recent=[row for row in ops if row['at']>=run['started_at']]
port=next(row for row in recent if row['op']=='fault' and row['result'].get('kind')=='port' and row['result'].get('active'))
fallback=port['result']['restart']['after'];binding=port['result']['_source_binding']
check(fallback['pid_bound_ready'] and fallback['ready']['port'] not in (17881,3000) and str(fallback['ready']['pid'])!=port['result']['restart']['before_pid'] and binding['source_sha256']==sha,'owned_port_contention_actual_fallback_listener')
release=next(row for row in recent if row['at']>port['at'] and row['op']=='fault' and row['result'].get('released')=='port')
probes=[row['result'] for row in recent if port['at']<row['at']<release['at'] and row['op']=='api']
check(any(v.get('status')==200 and v.get('body')=={'status':'ok','release':'v2'} for v in probes) and any(v.get('status')==200 and v.get('body')=={'ready':True} for v in probes),'real_https_while_preferred_port_held')
restart=next(row for row in recent if row['at']>release['at'] and row['op']=='fault' and 'after' in row['result'])
check(restart['result']['after']['pid_bound_ready'] and restart['result']['after']['ready']['port']==17881 and restart['result']['after']['current']['source_sha256']==sha,'preferred_port_and_edge_restored_after_holder_release')
spec=importlib.util.spec_from_file_location('controller',REPO/'scripts/level12-lab/controller.py');O=importlib.util.module_from_spec(spec);spec.loader.exec_module(O)
status=O.remote({'op':'status'});check(status['pid_bound_ready'] and status['app']['ActiveState']=='active' and status['edge']['ActiveState']=='active' and status['ready']['port']==17881 and status['current']['source_sha256']==sha and status['schema']=={'version':2,'journal':'wal','integrity':'ok'},'current_linux_service_schema_and_source_verified')
check(all(hashlib.sha256((P/n).read_bytes()).hexdigest()==v for n,v in status['current']['hashes'].items()),'current_candidate_bytes_equal_deployed_release')
for path in ['/healthz','/readyz']:
    check(O.api({'method':'GET','path':path,'role':'anonymous'})['status']==200,'current_https_'+path)
checkpoint=json.loads((P/'CHECKPOINT.json').read_text());anchor=O.api({'method':'GET','path':'/api/customers/'+checkpoint['baseline_customer_id'],'role':'admin'})
check(anchor['status']==200 and anchor['body']['customer']['segment']==docs['turn-c-resume-v2.json']['baseline']['body']['customer']['segment'],'original_customer_current_data_retained')
post_id=docs['turn-c-backup.json']['post_backup_customer']['body']['customer']['id']
post=O.api({'method':'GET','path':'/api/customers/'+post_id,'role':'admin'});check(post['status']==200 and post['body']['customer']['segment']=='vip','post_snapshot_vip_customer_still_present')
logs=O.remote({'op':'logs'});check(not logs['traceback'] and not logs['sensitive_marker'],'application_logs_remain_mechanical')
result=json.loads((P/'RESULT.json').read_text());check(result['phase']=='revision_recovery' and result.get('completed') is True and result.get('checkpoint_preserved') is True,'native_report_matches_completed_acceptance')
revision=max(R.glob('revision-grade-*.json'),key=lambda f:f.stat().st_mtime);ui=max(R.glob('ui-grade-*.json'),key=lambda f:f.stat().st_mtime)
for file in [revision,ui]:
    report=json.loads(file.read_text());check(report['passed'] and report['source_sha256']==sha,'independent_suite_matches_same_current_source')
value={'passed':True,'checks':checks,'release':'v2','source_sha256':sha,'fallback_port':fallback['ready']['port'],'final_port':status['ready']['port'],'snapshot_customers':n,'live_customers_at_clone_verification':n+1,'clone_active':False,'app_memory_bytes':int(status['app']['MemoryCurrent']),'edge_memory_bytes':int(status['edge']['MemoryCurrent']),'at':time.time()}
(R/'recovery-acceptance.json').write_text(json.dumps(value,indent=2));state=json.loads((R/'WORK-STATE.json').read_text());state['revision_independent_passed']=True;state['progress']['remaining_port_drill']=False;(R/'WORK-STATE.json').write_text(json.dumps(state,indent=2));print(json.dumps(value))
