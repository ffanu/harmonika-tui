"""No runtime claims from log text or client-supplied browser observations."""
from .common import fields,need,text,integer,digest,redacted_label
LOG_OPS=frozenset(('evidence.log.capture','evidence.stack.trace','web.backend.exception','asan.report.analyze','tsan.report.analyze','ubsan.report.analyze'))
BROWSER_OPS=frozenset(('web.frontend.console.error','web.frontend.network.error','web.frontend.cors.error','web.frontend.csp.violation','uiux.responsive.issue'))
PERF_OPS=frozenset(('perf.slow.response','perf.benchmark.compare','monitor.error.rate'))
def inspect_logs(op,v):
 import re
 fields(v,['logs']);logs=text(v['logs'],50000);events=[];frames=[]
 patterns=[('python_exception',r'\b(?:TypeError|ValueError|RuntimeError|KeyError|AttributeError|SyntaxError|MemoryError)\b'),('javascript_exception',r'\b(?:ReferenceError|RangeError|URIError)\b'),('fatal',r'\b(?:FATAL|panic|segmentation fault|OutOfMemoryError)\b'),('asan',r'AddressSanitizer: ([a-z-]+)'),('tsan',r'ThreadSanitizer: ([a-z -]+)'),('ubsan',r'runtime error:')]
 for number,line in enumerate(logs.splitlines(),1):
  for kind,pattern in patterns:
   if re.search(pattern,line,re.I):events.append({'type':kind,'line':number,'evidence_sha256':digest(line)})
  py=re.search(r'File "([^"\n]{1,300})", line (\d{1,7})',line)
  js=re.search(r'\(([^\n()]{1,300}):([0-9]{1,7}):[0-9]{1,7}\)',line)
  match=py or js
  if match:frames.append({'file_ref':redacted_label(match[1]),'line':int(match[2]),'evidence_sha256':digest(line)})
  need(len(events)+len(frames)<=400,'evidence_count_limit')
 if op=='evidence.stack.trace':data={'frames':frames,'exception_events':events}
 elif op=='web.backend.exception':data={'exceptions':events,'frames':frames}
 elif op.endswith('report.analyze'):
  wanted=op.split('.')[0];data={'sanitizer':wanted,'events':[x for x in events if x['type']==wanted],'frames':frames}
 else:data={'line_count':len(logs.splitlines()),'events':events,'frames':frames}
 return {'status':'analyzed_supplied_evidence','data':data,'input_sha256':digest(logs),'provenance':'caller_supplied_not_independently_verified','raw_log_returned':False,'host_logs_read':False,'root_cause_confirmed':False}
def inspect_browser(op,v):
 fields(v,['observations']);rows=v['observations'];need(type(rows) is list and len(rows)<=300,'invalid_observations');out=[]
 allowed={'console_error','network_error','cors_error','csp_violation','layout_overflow'}
 for row in rows:
  fields(row,['type','line','file','viewport_width','document_width'],['type']);need(type(row['type']) is str and row['type'] in allowed,'invalid_observation_type')
  item={'type':row['type']}
  if 'line' in row:item['line']=integer(row['line'],1)
  if 'file' in row:item['file_ref']=redacted_label(row['file'])
  if row['type']=='layout_overflow':
   item['viewport_width']=integer(row.get('viewport_width'),240,4000);item['document_width']=integer(row.get('document_width'),0,100000)
   item['overflow']=item['document_width']>item['viewport_width']
  else:need(not {'viewport_width','document_width'}&set(row),'irrelevant_observation_fields')
  out.append(item)
 wanted={'web.frontend.console.error':'console_error','web.frontend.network.error':'network_error','web.frontend.cors.error':'cors_error','web.frontend.csp.violation':'csp_violation','uiux.responsive.issue':'layout_overflow'}[op]
 return {'status':'analyzed_supplied_evidence','observations':[x for x in out if x['type']==wanted],'provenance':'caller_supplied_not_independently_verified','browser_launched':False,'limitations':['Use existing workflow.browser on supplied HTML for real isolated software Chromium evidence.']}
def inspect_performance(op,v):
 if op=='monitor.error.rate':
  fields(v,['total','errors','threshold_bps']);total=integer(v['total'],1,1000000000);errors=integer(v['errors'],0,total);threshold=integer(v['threshold_bps'],0,10000);rate=(errors*10000+total//2)//total
  return {'status':'analyzed_supplied_samples','error_rate_bps':rate,'threshold_exceeded':rate>threshold,'monitor_installed':False}
 fields(v,['samples_ms','threshold_ms','baseline_ms'],['samples_ms','threshold_ms']);rows=v['samples_ms'];need(type(rows) is list and 1<=len(rows)<=1000,'invalid_samples');rows=sorted(integer(x,0,3600000) for x in rows);threshold=integer(v['threshold_ms'],1,3600000)
 def p(bps):return rows[max(0,(len(rows)*bps+9999)//10000-1)]
 result={'sample_count':len(rows),'p50_ms':p(5000),'p95_ms':p(9500),'max_ms':rows[-1],'slow_count':sum(x>threshold for x in rows),'threshold_ms':threshold,'provenance':'caller_supplied_not_runtime_profile','profiler_attached':False}
 if op=='perf.benchmark.compare':
  baseline=integer(v.get('baseline_ms'),1,3600000);result.update(baseline_ms=baseline,p95_delta_ms=p(9500)-baseline,p95_ratio_bps=(p(9500)*10000+baseline//2)//baseline)
 else:need('baseline_ms' not in v,'unexpected_baseline')
 return {'status':'analyzed_supplied_samples',**result}
