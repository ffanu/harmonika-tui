"""ERP source-backed engine client. No new MCP tools or remote owner credentials."""
import argparse,base64,hashlib,importlib.util,io,json,os,stat,sys,time,uuid,zipfile
from pathlib import Path
s=importlib.util.spec_from_file_location('erp_catalog_client',Path(__file__).with_name('master_cli.py'));client=importlib.util.module_from_spec(s);s.loader.exec_module(client)
ANALYSIS_OPERATIONS=('finance.journal.entry','finance.general.ledger','finance.invoice','inventory.stock.management','finance.reconciliation','finance.depreciation','finance.budgeting','finance.multi.currency','report.customer.aging','report.vendor.aging','isp.billing.cycle','mfg.bom','mfg.mrp','mfg.production.costing','inventory.stock.opname','inventory.batch.tracking','procurement.three.way.match','retail.promotion','pos.cash.management','pm.time.tracking','mfg.production.scheduling','mfg.routing','mfg.scrap.management','mfg.quality.control','pos.transaction','crm.pipeline','crm.satisfaction.survey','pm.project.billing','pm.gantt.chart','pm.resource.allocation','pm.risk.management','pm.project.portfolio','pm.milestone','pm.issue.tracking','finance.financial.statement','finance.cashflow.statement','report.financial.statement','report.profit.loss','report.cash.flow','report.export.excel','hr.attendance','hr.leave.management','hr.payroll','hr.time.overtime','hr.expense.claim','hr.loan.management','procurement.rfq','procurement.vendor.performance')
PURE_SKILLS={'erp_all.plan','erp_all.generate','erp_all.catalog'}|{'erp_all.'+op for op in ANALYSIS_OPERATIONS}
def pure_request(skill,payload):
 # These ERP skills are pure planners/generators/calculations. Never generalize
 # admission-only retries to other registry tools or business write endpoints.
 request_id=uuid.uuid4().hex
 for attempt in range(4):
  value=client.request('POST','/invoke',{'skill_id':skill,'input':payload},idempotency_key=request_id)
  error=value.get('error') if isinstance(value,dict) else None
  if skill not in PURE_SKILLS or not isinstance(error,dict) or error.get('code')!='analysis_busy' or attempt==3:return value
  time.sleep(.25*(2**attempt))
def save_excel(result):
 from xml.etree import ElementTree as ET
 artifact=result.get('artifact',{})
 if artifact.get('name')!='harmonika-report.xlsx' or artifact.get('mime_type')!='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':raise ValueError('invalid_report_artifact')
 data=base64.b64decode(artifact['base64'],validate=True)
 if len(data)>180000 or hashlib.sha256(data).hexdigest()!=artifact.get('sha256'):raise ValueError('artifact_integrity_failed')
 allowed={'[Content_Types].xml','_rels/.rels','xl/workbook.xml','xl/_rels/workbook.xml.rels','xl/worksheets/sheet1.xml','xl/styles.xml','docProps/core.xml'}
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  infos=z.infolist()
  if len(infos)!=len(allowed) or {i.filename for i in infos}!=allowed or sum(i.file_size for i in infos)>1048576 or any(stat.S_ISLNK(i.external_attr>>16) or i.flag_bits&1 for i in infos):raise ValueError('unsafe_report_package')
  for name in allowed:
   raw=z.read(name)
   if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('unsafe_report_xml')
   try:root=ET.fromstring(raw)
   except (ET.ParseError,UnicodeError):raise ValueError('invalid_report_xml')
   if name in ('_rels/.rels','xl/_rels/workbook.xml.rels'):
    targets={'xl/workbook.xml','docProps/core.xml'} if name=='_rels/.rels' else {'worksheets/sheet1.xml','styles.xml'}
    if len(list(root))!=2 or {element.get('Target') for element in root}!=targets:raise ValueError('unsafe_report_xml')
   for element in root.iter():
    if element.tag.rsplit('}',1)[-1] in ('f','externalReference','oleObject','definedName','hyperlink','hyperlinks','connection','queryTable') or element.get('TargetMode','').lower()=='external':raise ValueError('unsafe_report_xml')
 base=Path(os.environ.get('HARMONIKA_DOWNLOADS_DIR',str(Path.home()/'Downloads/harmonika-ai')))/'xlsx'
 if not base.is_absolute() or any(p.is_symlink() for p in (base,*base.parents)):raise ValueError('unsafe_artifact_directory')
 base.mkdir(parents=True,exist_ok=True,mode=0o700);dest=base/('harmonika-report-'+uuid.uuid4().hex[:12]+'.xlsx')
 fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
 with os.fdopen(fd,'wb') as output:output.write(data)
 return {'status':'report_exported','artifact_path':str(dest),'sha256':artifact['sha256'],'row_count':result.get('row_count'),'column_count':result.get('column_count'),'formulas_enabled':False,'business_data_updated':False}
def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['plan','generate','catalog','analyze']);parser.add_argument('operation',nargs='?');args=parser.parse_args()
 if args.action=='catalog':payload={};skill='erp_all.catalog'
 else:
  raw=sys.stdin.buffer.read(65537)
  if len(raw)>65536:raise ValueError('input_too_large')
  payload=json.loads(raw)
  if type(payload) is not dict:raise ValueError('invalid_input')
  if args.action=='analyze':
   if args.operation not in ANALYSIS_OPERATIONS:raise ValueError('unsupported_analysis')
   skill='erp_all.'+args.operation;payload={'data':payload}
  else:skill='erp_all.'+args.action
 value=pure_request(skill,payload)
 if value.get('error'):return value
 result=value.get('result',value)
 if args.action=='catalog':
  counts={}
  for row in result.get('skills',[]):counts[row['status']]=counts.get(row['status'],0)+1
  return {'catalog_size':result.get('catalog_size'),'industry_profiles':result.get('industry_profiles'),'readiness':counts,'all_skills_operational':False,'callable_entrypoints':result.get('callable_entrypoints'),'orchestrators':result.get('orchestrators',[])}
 if args.action=='analyze' and args.operation=='report.export.excel' and 'artifact' in result.get('result',{}):return save_excel(result['result'])
 if args.action!='generate' or 'artifact' not in result:return result
 artifact=result['artifact'];raw=base64.b64decode(artifact['zip_base64'],validate=True)
 if len(raw)>2*1024*1024 or hashlib.sha256(raw).hexdigest()!=artifact['sha256']:raise ValueError('artifact_integrity_failed')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  infos=z.infolist()
  if len(infos)>100 or sum(i.file_size for i in infos)>2*1024*1024:raise ValueError('artifact_limit')
  seen=set()
  for info in infos:
   path=Path(info.filename)
   if path.is_absolute() or '..' in path.parts or '\\' in info.filename or info.filename in seen or stat.S_ISLNK(info.external_attr>>16):raise ValueError('unsafe_artifact')
   seen.add(info.filename)
  if not {'package.json','apps/api/src/main.ts','apps/web/app/page.tsx'}<=seen:raise ValueError('invalid_artifact')
 base=Path(os.environ.get('HARMONIKA_DOWNLOADS_DIR',str(Path.home()/'Downloads/harmonika-ai')))/'zip'
 if not base.is_absolute() or any(p.is_symlink() for p in (base,*base.parents)):raise ValueError('unsafe_artifact_directory')
 base.mkdir(parents=True,exist_ok=True,mode=0o700);dest=base/('harmonika-erp-'+result['plan']['industry']+'-'+uuid.uuid4().hex[:12]+'.zip')
 fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw)
 return {'status':result['status'],'industry':result['plan']['industry'],'artifact_path':str(dest),'sha256':artifact['sha256'],'source_file_count':result.get('source_file_count'),'built':False,'deployed':False,'tests_run_for_this_artifact':False,'needs_owner_configuration':True}
if __name__=='__main__':
 try:print(json.dumps(main(),ensure_ascii=False))
 except Exception as e:
  code=str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'erp_client_failed';print(json.dumps({'error':{'code':code}}));sys.exit(2)
