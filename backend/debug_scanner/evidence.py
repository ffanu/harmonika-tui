"""Evidence-linked supplied RCA. Never manufacture a cause or an independent check."""
from .common import fields,need,text,integer,digest,safe_id,redact
EVIDENCE_OPS=frozenset(('evidence.hash.compute','evidence.timeline.build'))
RCA_OPS=frozenset(('rca.five.why','rca.symptom.vs.root','rca.report.generate','solution.verify.evidence'))
def evidence_operation(op,v):
 if op=='evidence.hash.compute':
  fields(v,['content']);return {'status':'hashed_supplied_content','sha256':digest(text(v['content'],50000)),'host_file_read':False,'legal_chain_of_custody_certified':False}
 from datetime import datetime
 fields(v,['events']);need(type(v['events']) is list and len(v['events'])<=200,'invalid_events');rows=[];seen=set()
 for row in v['events']:
  fields(row,['id','timestamp','kind','sha256']);key=safe_id(row['id']);need(key not in seen,'duplicate_event');seen.add(key);stamp=text(row['timestamp'],40)
  try:parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
  except ValueError:raise ValueError('invalid_timestamp') from None
  need(parsed.tzinfo is not None and parsed.utcoffset() is not None,'timezone_required');kind=safe_id(row['kind']);sha=text(row['sha256'],64);need(__import__('re').fullmatch('[0-9a-f]{64}',sha),'invalid_evidence_hash');rows.append({'id':key,'timestamp':stamp,'kind':kind,'sha256':sha,'sort_key':parsed.timestamp()})
 rows.sort(key=lambda r:(r['sort_key'],r['id']))
 for r in rows:del r['sort_key']
 return {'status':'ordered_supplied_evidence','events':rows,'timestamps_independently_verified':False,'legal_chain_of_custody_certified':False}
def analyze_causes(op,v):
 import re
 fields(v,['evidence','causes','symptom'],['evidence','causes']);need(type(v['evidence']) is list and len(v['evidence'])<=100 and type(v['causes']) is list and len(v['causes'])<=20,'invalid_evidence_or_causes');evidence={}
 for row in v['evidence']:
  fields(row,['id','method','sha256']);key=safe_id(row['id']);need(key not in evidence,'duplicate_evidence');method=safe_id(row['method']);need(re.fullmatch('[0-9a-f]{64}',str(row['sha256'])),'invalid_evidence_hash');evidence[key]={'method':method,'sha256':row['sha256']}
 causes=[];seen=set()
 for row in v['causes']:
  fields(row,['id','parent_id','evidence_ids']);key=safe_id(row['id']);need(key not in seen,'duplicate_cause');parent=row['parent_id'];need(parent is None or type(parent) is str and parent in seen,'parent_must_precede_child');seen.add(key);refs=row['evidence_ids'];need(type(refs) is list and len(refs)<=10 and all(type(x) is str for x in refs) and len(set(refs))==len(refs) and all(x in evidence for x in refs),'invalid_evidence_refs');methods=sorted({evidence[x]['method'] for x in refs});causes.append({'id':key,'parent_id':parent,'evidence_ids':refs,'independent_method_labels':methods,'two_method_labels_supplied':len(methods)>=2})
 if 'symptom' in v:need(type(v['symptom']) is str and v['symptom'] in seen,'unknown_symptom')
 paths={}
 for cause in causes:paths[cause['id']]=paths.get(cause['parent_id'],[])+[cause['id']]
 specific={'why_chains':list(paths.values())} if op=='rca.five.why' else {'symptom_id':v.get('symptom'),'cause_candidates':[c['id'] for c in causes if c['two_method_labels_supplied']]} if op=='rca.symptom.vs.root' else {'supported_cause_ids':[c['id'] for c in causes if c['two_method_labels_supplied']],'unsupported_cause_ids':[c['id'] for c in causes if not c['two_method_labels_supplied']]} if op=='solution.verify.evidence' else {'evidence_count':len(evidence),'cause_count':len(causes)}
 return {**specific,'status':'evidence_linked' if causes and all(c['two_method_labels_supplied'] for c in causes) else 'insufficient_evidence','causes':causes,'root_cause_confirmed':False,'provenance':'caller_supplied_evidence_and_cause_graph','independence_verified':False,'why_chain_generated':False,'automatic_fix_permitted':False,'limitations':['Two method labels are an input coverage check, not independent verification of a hypothesis. No causal statement is inferred.']}
