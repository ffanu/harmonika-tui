"""Exact scanner routing. Source catalog membership never means implementation."""
import copy,json
from pathlib import Path
from .common import bounded,fields,need,digest,ScannerError
from .targets import TARGET_OPS,SCOPE_OPS,PROBE_OPS,classify,scope_check,http_probe
from .observations import LOG_OPS,BROWSER_OPS,PERF_OPS,inspect_logs,inspect_browser,inspect_performance
from .evidence import EVIDENCE_OPS,RCA_OPS,evidence_operation,analyze_causes
from .reports import REPORT_OPS,report
from .static import STATIC_OPS,static_scan
from . import logic_checks,configuration_checks,advanced_static
from .advanced_static import ADVANCED_STATIC_OPS,advanced_static_scan
from .logic_checks import LOGIC_OPS,logic_scan,logic_contract,logic_schema
from .configuration_checks import CONFIGURATION_OPS,configuration_scan,configuration_schema
SOURCE=json.loads((Path(__file__).parent/'catalog.json').read_text())
EXTRA_SOURCE_OPS=frozenset(('sec.owasp.top10',))
ACTIVE_OPS=TARGET_OPS|SCOPE_OPS|PROBE_OPS|LOG_OPS|BROWSER_OPS|PERF_OPS|EVIDENCE_OPS|RCA_OPS|REPORT_OPS|STATIC_OPS|EXTRA_SOURCE_OPS|LOGIC_OPS|CONFIGURATION_OPS|ADVANCED_STATIC_OPS

def input_schema(op):
 def obj(properties,required):return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
 def string(n):return {'type':'string','maxLength':n}
 file=obj({'path':string(240),'content':string(60000)},['path','content'])
 source={'type':'array','items':file,'maxItems':20}
 if op=='catalog':return obj({},[])
 if op in STATIC_OPS|EXTRA_SOURCE_OPS|ADVANCED_STATIC_OPS:data=obj({'files':source},['files'])
 elif op in LOGIC_OPS:data=logic_schema(op)
 elif op in CONFIGURATION_OPS:data=configuration_schema(op)
 elif op in TARGET_OPS:data=obj({'target':string(500)},['target'])
 elif op in SCOPE_OPS|PROBE_OPS:data=obj({'target_id':string(80),'path':string(200)},['target_id'])
 elif op in LOG_OPS:data=obj({'logs':string(50000)},['logs'])
 elif op in BROWSER_OPS:data=obj({'observations':{'type':'array','maxItems':300,'items':{'type':'object','additionalProperties':True,'description':'type console_error|network_error|cors_error|csp_violation|layout_overflow; optional file,line; overflow requires viewport_width,document_width.'}}},['observations'])
 elif op=='evidence.hash.compute':data=obj({'content':string(50000)},['content'])
 elif op in RCA_OPS:data={'type':'object','additionalProperties':True,'description':'Exact fields: evidence:[{id,method,sha256}], causes:[{id,parent_id:null or earlier cause id,evidence_ids:[id]}], optional symptom:cause id. No raw cause narrative or confidence inference.'}
 elif op=='evidence.timeline.build':data=obj({'events':{'type':'array','maxItems':200,'items':obj({'id':string(80),'timestamp':string(40),'kind':string(80),'sha256':string(64)},['id','timestamp','kind','sha256'])}},['events'])
 elif op in REPORT_OPS:
  finding=obj({'id':string(80),'rule_id':string(80),'severity':{'type':'string','enum':['critical','high','medium','low','info']},'line':{'type':'integer','minimum':1,'maximum':1000000},'sha256':string(64)},['id','rule_id','severity','sha256'])
  data=obj({'findings':{'type':'array','maxItems':200,'items':finding},'coverage_complete':{'type':'boolean'},**({'format':{'type':'string','enum':['markdown','pdf']}} if op!='cicd.block.if.critical' else {})},['findings'])
 elif op=='monitor.error.rate':data=obj({'total':{'type':'integer','minimum':1,'maximum':1000000000},'errors':{'type':'integer','minimum':0,'maximum':1000000000},'threshold_bps':{'type':'integer','minimum':0,'maximum':10000}},['total','errors','threshold_bps'])
 elif op in PERF_OPS:data=obj({'samples_ms':{'type':'array','minItems':1,'maxItems':1000,'items':{'type':'integer','minimum':0,'maximum':3600000}},'threshold_ms':{'type':'integer','minimum':1,'maximum':3600000},**({'baseline_ms':{'type':'integer','minimum':1,'maximum':3600000}} if op=='perf.benchmark.compare' else {})},['samples_ms','threshold_ms']+(['baseline_ms'] if op=='perf.benchmark.compare' else []))
 else:data=obj({'files':source,'logs':string(50000),'browser_observations':{'type':'array','maxItems':300,'items':{'type':'object','additionalProperties':True}},'target_id':string(80),'path':string(200),'report_format':{'type':'string','enum':['markdown','pdf']},'checks':{'type':'array','maxItems':4,'items':obj({'operation':{'type':'string','enum':sorted(LOGIC_OPS|CONFIGURATION_OPS)},'input':{'type':'object','additionalProperties':True}},['operation','input'])}},[])
 return obj({'data':data},['data'])

def mode(op):
 if op in TARGET_OPS:return 'classification_hint_only'
 if op in SCOPE_OPS:return 'operator_target_scope_check'
 if op in PROBE_OPS:return 'registered_target_single_get_no_exploit'
 if op in STATIC_OPS|EXTRA_SOURCE_OPS|ADVANCED_STATIC_OPS:return 'supplied_source_static_candidates_only'
 if op in BROWSER_OPS:return 'supplied_browser_observations_not_browser_launch'
 if op in LOG_OPS|EVIDENCE_OPS|PERF_OPS|RCA_OPS:return 'supplied_evidence_analysis'
 if op in REPORT_OPS:return 'redacted_report_or_supplied_ci_decision'
 if op in LOGIC_OPS:return 'declared_model_or_observed_trace_validation_no_execution'
 if op in CONFIGURATION_OPS:return 'offline_configuration_or_source_checks_no_auth_verification'
 return 'bounded_diagnostic_pipeline'
def capabilities():
 rows=[]
 for source in SOURCE['skills']:
  row=copy.deepcopy(source);op=row['operation'];active=op in ACTIVE_OPS
  reason=None if active else 'registered_runtime_or_toolchain_required' if op.startswith(('dast.','mobile.','desktop.','iot.','net.','fix.','monitor.','cicd.','kb.','memory.','crash.','compliance.')) else 'implementation_missing'
  row.update(id='debug_scan.'+op,status='available' if active else 'unavailable',mode=mode(op) if active else None,reason=reason,**({'input_schema':input_schema(op)} if active else {}));rows.append(row)
 return {'schema_version':1,'engine':'harmonika-debug-security-scanner','source_sha256':SOURCE['source_sha256'],'source_lines':SOURCE['line_count'],'header_declared':SOURCE['header_declared'],'catalog_size':len(rows),'callable_entrypoints':len(ACTIVE_OPS)+2,'orchestrators':['debug_scan.catalog','debug_scan.scan'],'readiness':{'available':len(ACTIVE_OPS),'unavailable':len(rows)-len(ACTIVE_OPS)},'skills':rows,'features':{'source_static_analysis':True,'operator_registered_http_probe':True,'arbitrary_url_fetch':False,'browser_execution':'use_existing_networkless_workflow.browser','redacted_pdf_reports':True,'confirmed_exploit_detection':False,'autonomous_exploitation':False,'automatic_fix_or_deploy':False,'compliance_certified':False,'private_customer_database_access':False,'scheduled_monitor_installed':False},'limits':{'input_bytes':65536,'output_bytes':240000,'probe_timeout_outer_seconds':15,'redirects':0},'all_290_operational':False}
def call(operation,v):
 bounded(v);need(type(operation) is str,'invalid_operation');op=operation.removeprefix('debug_scan.')
 if op=='catalog':fields(v,[]);return capabilities()
 if op=='scan':return pipeline(v)
 need(op in ACTIVE_OPS,'scanner_skill_not_implemented')
 if op in TARGET_OPS:r=classify(op,v)
 elif op in SCOPE_OPS:r=scope_check(op,v)
 elif op in PROBE_OPS:r=http_probe(op,v)
 elif op in LOGIC_OPS:r=logic_scan(op,v)
 elif op in CONFIGURATION_OPS:r=configuration_scan(op,v)
 elif op in ADVANCED_STATIC_OPS:r=advanced_static_scan(op,v)
 elif op in STATIC_OPS:r=static_scan(op,v)
 elif op in EXTRA_SOURCE_OPS:
  r=static_scan('sast.code.review',v);r={**r,'owasp_assessment_complete':False,'dynamic_security_tests_performed':False,'coverage_kind':'partial_CWE_rule_candidates_not_complete_OWASP_assessment'}
 elif op in LOG_OPS:r=inspect_logs(op,v)
 elif op in BROWSER_OPS:r=inspect_browser(op,v)
 elif op in PERF_OPS:r=inspect_performance(op,v)
 elif op in EVIDENCE_OPS:r=evidence_operation(op,v)
 elif op in RCA_OPS:r=analyze_causes(op,v)
 elif op in REPORT_OPS:r=report(op,v)
 return wrap_result(op,r,mode(op))

def wrap_result(op,r,execution_mode):
 need(type(r) is dict,'invalid_scanner_output');result={'operation':op,'execution_mode':execution_mode,'result':r,'business_data_modified':False,'production_fix_applied':False,'raw_input_logged':False}
 need(len(json.dumps(result,ensure_ascii=True,allow_nan=False).encode())<=240000,'scanner_output_limit');return result

def pipeline_logic_issues(data):
 issues=list(data.get('violations',[]))
 if data.get('cycle_detected'):issues.append({'rule_id':'WORKFLOW.CYCLE_DETECTED'})
 issues.extend({'rule_id':p['rule_id'],'index':i} for i,p in enumerate(data.get('conflicting_commit_pairs',[])))
 issues.extend({'rule_id':'CASE.OBSERVED_MISMATCH','index':i} for i,c in enumerate(data.get('cases',[])) if not c['matches'])
 return issues

def pipeline(v):
 fields(v,['files','logs','browser_observations','target_id','path','report_format','checks'],[]);need(any(v.get(k) for k in ('files','logs','browser_observations','target_id','checks')),'scan_evidence_required');need('path' not in v or 'target_id' in v,'target_id_required');findings=[];parts={};degraded=False
 if 'files' in v:
  parts['static']=static_scan('sast.code.review',{'files':v['files']});findings=parts['static'].get('findings',[]);parts['static']['pipeline_findings_truncated']=len(findings)>100;parts['static']['findings']=findings[:100];findings=findings[:100]
 if 'files' in v:
  parts['advanced_static']={}
  for operation in sorted(ADVANCED_STATIC_OPS):
   check=advanced_static_scan(operation,{'files':v['files']});rows=check.get('findings',[]);check['pipeline_findings_truncated']=len(rows)>25;check['findings']=rows[:25];findings.extend(rows[:25]);parts['advanced_static'][operation]=check
 if 'logs' in v:parts['logs']=inspect_logs('web.backend.exception',{'logs':v['logs']})
 if 'browser_observations' in v:parts['browser']=inspect_browser('web.frontend.console.error',{'observations':v['browser_observations']})
 if 'target_id' in v:
  probe=http_probe('dast.reproduce',{k:v[k] for k in ('target_id','path') if k in v});parts['http_probe']=probe;degraded=probe['status']=='degraded'
 if 'checks' in v:
  checks=v['checks'];need(type(checks) is list and len(checks)<=4,'scan_check_limit');parts['checks']=[]
  for check in checks:
   fields(check,['operation','input']);op=check['operation'];need(type(op) is str and op in LOGIC_OPS|CONFIGURATION_OPS,'scan_check_not_allowed');data=check['input'];bounded(data)
   result=logic_scan(op,data) if op in LOGIC_OPS else configuration_scan(op,data);parts['checks'].append({'operation':op,'result':result});findings.extend(result.get('findings',[]))
   for issue in pipeline_logic_issues(result.get('data',{})):findings.append({'rule_id':issue['rule_id'],'severity':'medium','evidence':{'file':'declared_model','line':issue.get('index',issue.get('record_index',0))+1,'sha256':digest(json.dumps(data,sort_keys=True,ensure_ascii=True))},'confidence_kind':'supplied_model_or_trace_only','message':'Violation in the supplied model/trace; no business code was executed.'})
 total=len(findings);findings=findings[:100]
 normalized=[]
 for index,f in enumerate(findings):
  evidence=f.get('evidence',{});normalized.append({'id':'F%04d'%index,'rule_id':f['rule_id'],'severity':f['severity'],'line':evidence.get('line',1),'sha256':evidence['sha256']})
 need(len(normalized)<=200,'combined_finding_limit')
 doc=report('audit.report.generate',{'findings':normalized,'format':v.get('report_format','markdown'),'coverage_complete':False})
 result={'status':'degraded' if degraded else 'completed_with_limited_coverage','run_ref':digest(json.dumps(v,sort_keys=True,ensure_ascii=True)),'parts':parts,'report':doc,'reported_findings_truncated':total>100,'complete_security_assessment':False,'automatic_fix_applied':False,'limitations':['Static candidates and imported observations are not confirmed exploits or independent root-cause verification. Unimplemented categories remain visible in catalog.']}
 need(len(json.dumps(result,ensure_ascii=True).encode())<=240000,'scanner_output_limit');return result
