"""Exact operations IDs; handler registration never proves connector readiness."""
SPECS={
 'engine.diagnose':('Registered service and graphics connectivity; mechanical results only.',False,False),
 'skills.selftest':('Execute isolated synthetic checks; positive counts and explicit missing tools.',False,False),
 'security.scan':('Run configured Semgrep or Trivy against a registered project.',True,False),
 'browser.regression':('Run real browser assertions and private traces on registered static HTML.',True,False),
 'database.audit':('Read-only registered database schema, indexes and named query plans.',True,False),
 'license.usage':('Own license aggregates; all licenses require administrator role.',False,False),
 'job.prepare':('Persist operation identity before effects. Does not execute anything.',True,False),
 'job.status':('Read an existing owned job.',True,False),
 'job.resume':('Resume observation of the same job without replaying effects.',True,False),
 'job.reconcile':('Observe the same durable job; caller evidence cannot certify unknown effects.',True,False),
 'deploy.preview':('Prepare and hash a registered deployment plan without applying it.',True,True),
 'deploy.apply':('Apply the reviewed plan and verify actual health with guarded recovery.',True,True),
 'deploy.reconcile':('Reconcile an unknown deployment using actual pointer and registered HTTP health.',True,True),
 'deploy.rollback_preview':('Preview a rollback bound to the current release and original owned job.',True,True),
 'artifact.read':('Read a bounded chunk of an owned unexpired browser artifact.',True,False),
 'deploy.rollback':('Restore the registered previous release only with current-release CAS.',True,True),
}
SPECS={'operations.'+key:value for key,value in SPECS.items()}
IDS=frozenset(SPECS)
def descriptors():
 return [{'id':sid,'description':text,'engine':'operations','status':'context_required' if project else 'available','execution_target':'shared_operations_backend','access':['api','tui'],'input_schema':{'type':'object'},'requires_registered_project':project,'requires_deploy_permission':write,'limitations':['Toolchain, project access and actual verification are checked at invocation.']} for sid,(text,project,write) in SPECS.items()]

# Operation-specific DTOs make the available tools usable without guessing fields.
STR={'type':'string','minLength':1,'maxLength':128}
HASH={'type':'string','pattern':'^[a-f0-9]{64}$'}
PROJECT={'type':'string','pattern':'^[a-z][a-z0-9_-]{0,63}$'}
SCHEMAS={
 'engine.diagnose':({},[]),
 'skills.selftest':({'suite':{'type':'string','enum':['core','security','browser']}},[]),
 'license.usage':({'scope':{'type':'string','enum':['self','all']},'days':{'type':'integer','minimum':1,'maximum':90}},[]),
 'security.scan':({'scanner':{'type':'string','enum':['semgrep','trivy']},'mode':{'type':'string','enum':['vuln','misconfig','secret']},'path':{'type':'string','maxLength':400}},['scanner']),
 'browser.regression':({'path':{'type':'string','maxLength':400},'steps':{'type':'array','minItems':1,'maxItems':24,'items':{'type':'object','properties':{'action':{'type':'string','enum':['click','fill','check','select','assert_visible','assert_text','assert_count','assert_value']},'selector':{'type':'object','properties':{'by':{'type':'string','enum':['css','test_id','role']},'value':{'type':'string','maxLength':200},'name':{'type':'string','maxLength':200}},'required':['by','value'],'additionalProperties':False},'value':{'type':'string','maxLength':1000},'count':{'type':'integer','minimum':0,'maximum':1000}},'required':['action','selector'],'additionalProperties':False}},'viewport':{'type':'object','properties':{'width':{'type':'integer','minimum':320,'maximum':1920},'height':{'type':'integer','minimum':320,'maximum':1080}},'required':['width','height'],'additionalProperties':False}},['path','steps']),
 'database.audit':({'database':STR,'query':STR},['database']),
 'job.prepare':({'kind':{'type':'string','enum':['tool.execution','browser.audit','code.audit','diagnostic','selftest']},'idempotency_key':STR,'request_digest':HASH},['kind','idempotency_key','request_digest']),
 'job.status':({'job_ref':STR},['job_ref']),
 'job.resume':({'job_ref':STR},['job_ref']),
 'job.reconcile':({'job_ref':STR,'expected_version':{'type':'integer','minimum':0}},['job_ref','expected_version']),
 'deploy.reconcile':({'alias':STR,'job_ref':STR,'expected_version':{'type':'integer','minimum':0}},['alias','job_ref','expected_version']),
 'deploy.rollback_preview':({'alias':STR,'job_ref':STR},['alias','job_ref']),
 'artifact.read':({'artifact_id':{'type':'string','maxLength':100},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':65536}},['artifact_id']),
 'deploy.preview':({'alias':STR,'artifact':{'type':'string','maxLength':400},'expected_artifact_sha256':HASH},['alias','artifact']),
 'deploy.apply':({'alias':STR,'artifact':{'type':'string','maxLength':400},'expected_plan_sha256':HASH,'ack':{'type':'boolean'},'idempotency_key':STR},['alias','artifact','expected_plan_sha256','ack','idempotency_key']),
 'deploy.rollback':({'alias':STR,'job_ref':STR,'expected_plan_sha256':HASH,'ack':{'type':'boolean'},'idempotency_key':STR},['alias','job_ref','expected_plan_sha256','ack','idempotency_key']),
}
_base_descriptors=descriptors
def descriptors():
 result=_base_descriptors()
 for row in result:
  properties,required=SCHEMAS[row['id'].removeprefix('operations.')]
  properties=dict(properties);required=list(required)
  if row['requires_registered_project']:properties['project']=PROJECT;required.append('project')
  row['input_schema']={'type':'object','properties':properties,'required':required,'additionalProperties':False}
 return result
