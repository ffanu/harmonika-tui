"""Shared operations dispatcher. Identity comes from the authenticated gateway."""
import time
from pathlib import Path
from .catalog import IDS,SPECS,descriptors
from .common import fields,authorization,require,OpsError,private_path
from .telemetry import Telemetry
class Engine:
 def __init__(self,config,key):
  require(len(key)==32,'invalid_state_key',503)
  self.config=config;self.key=key;self.root=private_path(config['state_root'],directory=True,create=True)
  private_path(self.root/'artifacts',directory=True,create=True)
  self.telemetry=Telemetry(self.root/'telemetry',key)
 def capabilities(self):return {'ok':True,'service':'harmonika-operations','schema_version':1,'skills':descriptors(),'private_data_in_telemetry':False}
 def invoke(self,principal,sid,payload):
  require(type(sid) is str and sid in IDS,'unknown_skill',404);require(type(payload) is dict,'invalid_input')
  grant=authorization(self.config,principal);project=payload.get('project')
  if SPECS[sid][1]:
   require(type(project) is str and bool(project),'project_required')
   authorization(self.config,principal,project)
  else:require('project' not in payload,'unexpected_project')
  if SPECS[sid][2]:require(grant.get('role')=='admin' or 'deploy' in grant.get('permissions',[]),'deployment_permission_required',403)
  args={k:v for k,v in payload.items() if k!='project'}
  cfg=dict(self.config)
  if project:
   p=self.config['projects'][project];cfg.update(p);cfg['databases']=p.get('databases',{});cfg['project_root']=p['root'];cfg['artifacts_root']=str(private_path(self.root/'artifacts'/self._ref(principal,project),directory=True,create=True))
  operation=sid.removeprefix('operations.')
  started=time.monotonic();state='failed';error='none'
  try:
   if operation=='engine.diagnose':
    from .diagnostics import diagnose
    result=diagnose(cfg,args);result['recent_operations']=self.telemetry.summary(principal)
   elif operation=='skills.selftest':
    from .selftest import run
    result=run(cfg,args)
   elif operation=='license.usage':
    from .licenses import usage
    result=usage(cfg,principal,args,admin=grant.get('role')=='admin')
   elif operation=='artifact.read':
    from .artifacts import read
    result=read(cfg,args)
   elif operation=='database.audit':
    from .database import audit
    result=audit(cfg,args)
   elif operation=='security.scan':
    from .scanners import scan
    result=scan(cfg,args)
   elif operation=='browser.regression':
    from .browser import run
    result=run(cfg,args)
   elif operation.startswith('job.'):
    from .jobs import JobStore
    store=JobStore(self.root/'jobs',self.key)
    action=operation.split('.')[1]
    if action=='prepare':
     fields(args,{'kind','idempotency_key','request_digest'},('kind','idempotency_key','request_digest'));require(args['kind'] in ('tool.execution','browser.audit','code.audit','diagnostic','selftest'),'invalid_job_kind');result=store.prepare(principal,project,**args)
    elif action in ('status','resume'):
     fields(args,{'job_ref'},('job_ref',));result=(store.get if action=='status' else store.resume)(principal,project,args['job_ref'])
    else:
     fields(args,{'job_ref','expected_version'},('job_ref','expected_version'));result=store.resume(principal,project,args['job_ref']);require(type(args['expected_version']) is int and result['version']==args['expected_version'],'job_version_conflict',409);result={**result,'reconciliation_source':'durable_observation_only','mutation_replayed':False,'requires_connector_observation':result['state']=='unknown'}
   else:
    from .deployment import DeploymentEngine
    from .jobs import JobStore
    deployment=DeploymentEngine(self.root/'deployment',self.key,cfg,job_store=JobStore(self.root/'jobs',self.key))
    action=operation.split('.')[1]
    allowed={'preview':{'alias','artifact','expected_artifact_sha256'},'apply':{'alias','artifact','expected_plan_sha256','ack','idempotency_key'},'rollback':{'alias','job_ref','expected_plan_sha256','ack','idempotency_key'},'rollback_preview':{'alias','job_ref'},'reconcile':{'alias','job_ref','expected_version'}}[action]
    fields(args,allowed,allowed-{'expected_artifact_sha256'} if action=='preview' else allowed);result=getattr(deployment,action)(principal,project,**args)
   require(type(result) is dict,'invalid_handler_response',502)
   state=result.get('status',result.get('state','completed'))
   return {'ok':result.get('ok',True) is True and state not in ('failed','rolled_back','rollback_failed','unknown','needs_toolchain','degraded','blocked','partial','inconclusive'),'skill_id':sid,'result':result,'evidence_source':'durable_observation' if operation=='job.reconcile' else 'server_executed','retry_safe':operation not in ('deploy.apply','deploy.rollback','job.reconcile')}
  except OpsError as e:
   error='access_denied' if e.status==403 else 'invalid_input' if e.status==400 else 'operation_failed';raise
  except ValueError as e:
   code=str(e);code=code if code.replace('_','').isalnum() and len(code)<80 else 'operation_failed'
   error='operation_failed';raise OpsError(code,409) from None
  finally:
   try:self.telemetry.record(principal,sid,state,(time.monotonic()-started)*1000,error)
   except Exception:pass
 def _ref(self,principal,project):
  import hmac,hashlib
  return hmac.new(self.key,(principal+'\0'+project).encode(),hashlib.sha256).hexdigest()
