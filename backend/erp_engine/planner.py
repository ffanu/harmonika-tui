"""Deterministic source-backed ERP planning, no customer data/network/model claims."""
import copy,hashlib,json,re,unicodedata
from pathlib import Path
ROOT=Path(__file__).parent
PROFILES=json.loads((ROOT/'industries.json').read_text())
CATALOG=json.loads((ROOT/'catalog.json').read_text())
BY_ID={p['id']:p for p in PROFILES}
DEPS={'finance':['core'],'inventory':['core'],'sales':['finance','inventory'],'pos':['sales'],'procurement':['inventory','finance'],'crm':['core'],'isp':['sales','crm','field_ops'],'field_ops':['core'],'manufacturing':['inventory','procurement'],'projects':['core'],'food':['pos'],'pharmacy':['inventory'],'ecommerce':['sales'],'construction':['projects','procurement'],'hr':['core'],'integration':['core'],'analytics':['finance']}
LABELS={'core':'Pengaturan','finance':'Keuangan','inventory':'Persediaan','sales':'Penjualan','crm':'Prospek','isp':'Layanan Internet','field_ops':'Teknisi','pos':'Kasir','manufacturing':'Produksi','procurement':'Pembelian','hr':'SDM','projects':'Proyek','food':'Restoran'}
CORE_RUNTIME={'core','finance','sales','inventory'}
class ERPError(ValueError):
 def __init__(self,code):self.code=code;super().__init__(code)
def require(ok,code):
 if not ok:raise ERPError(code)
def normalize(s):return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()).strip()
def detect(spec,industry=None):
 require(type(spec) is str and 1<=len(spec)<=16000 and bool(spec.strip()),'invalid_spec')
 if industry is not None:require(type(industry) is str and industry in BY_ID,'unknown_industry')
 text=normalize(spec);negated=[]
 for p in PROFILES:
  if any(re.search(r'\b(?:bukan|jangan|tanpa|not) (?:usaha |industri |bisnis )?'+re.escape(normalize(k))+r'\b',text) for k in p['keywords']):negated.append(p['id'])
 rows=[]
 for p in PROFILES:
  evidence=[k for k in p['keywords'] if re.search(r'\b'+re.escape(normalize(k))+r'\b',text)]
  score=sum(4 if len(normalize(k).split())>1 else 2 for k in evidence)
  if p['id'] in negated:score=0;evidence=[]
  if score:rows.append({'industry':p['id'],'score':score,'matched_keywords':evidence})
 rows.sort(key=lambda r:(-r['score'],r['industry']))
 ambiguous=not rows or (len(rows)>1 and rows[0]['score']==rows[1]['score'])
 chosen=industry or (rows[0]['industry'] if rows and not ambiguous else None)
 confidence=1 if industry else (min(.95,.5+rows[0]['score']*.04) if chosen else 0)
 counts={}
 for field,words in [('customers','pelanggan|subscriber|customers'),('employees','karyawan|pegawai|employees'),('locations','cabang|toko|outlet')]:
  found=re.search(r'\b(\d{1,8})\s*(?:'+words+r')\b',text)
  if found:counts[field]=int(found[1])
 scale='large' if counts.get('employees',0)>=250 or counts.get('customers',0)>=10000 else 'medium' if counts.get('employees',0)>=50 or counts.get('customers',0)>=1000 else 'small' if counts else 'unspecified'
 return {'industry':chosen,'candidates':rows[:3],'confidence':confidence,'confidence_kind':'keyword_heuristic_not_probability','clarification_required':chosen is None,'clarification_question':'Bidang usaha utamanya apa: ISP, toko, manufaktur, jasa, atau bidang lainnya?' if chosen is None else None,'counts':counts,'scale':scale,'scale_kind':'planning_heuristic_not_legal_classification','region':'Indonesia' if re.search(r'\b(indonesia|jakarta|depok|bandung|bogor)\b',text) else 'unspecified','negated_industries':negated}
def modules(industry,optional=None,exclude=None):
 require(type(industry) is str and industry in BY_ID,'unknown_industry');optional=[] if optional is None else optional;exclude=[] if exclude is None else exclude
 require(type(optional) is list and type(exclude) is list and len(optional)<=40 and len(exclude)<=40,'invalid_modules')
 require(all(type(m) is str for m in optional+exclude),'invalid_module')
 require(len(set(optional))==len(optional) and len(set(exclude))==len(exclude),'duplicate_module')
 require(not set(optional)&set(exclude),'module_selection_conflict')
 all_ids=set(DEPS)|set(LABELS)|{'core'}|{m for p in PROFILES for m in p['required_modules']+p['optional_modules']}
 require(type(optional) is list and type(exclude) is list and set(optional+exclude)<=all_ids,'unknown_module')
 required=BY_ID[industry]['required_modules'];require(not set(required)&set(exclude),'required_module_excluded')
 selected=[];visiting=set()
 def visit(module):
  require(module not in visiting,'module_dependency_cycle')
  if module in selected:return
  visiting.add(module)
  for dep in DEPS.get(module,['core'] if module!='core' else []):
   require(dep not in exclude,'required_dependency_excluded');visit(dep)
  visiting.remove(module);selected.append(module)
 for module in required+optional:visit(module)
 mandatory=set()
 def mark(module):
  if module in mandatory:return
  mandatory.add(module)
  for dep in DEPS.get(module,['core'] if module!='core' else []):mark(dep)
 for module in required:mark(module)
 return [{'id':m,'name':LABELS.get(m,m.replace('_',' ').title()),'required':m in mandatory,'selection_reason':'industry_required' if m in required else 'required_dependency' if m in mandatory else 'optional','runtime':'core_starter' if m in CORE_RUNTIME else 'schema_scaffold_only','dependencies':DEPS.get(m,[])} for m in selected]
def contrast(hexcolor):
 def channel(v):return v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4
 c=[channel(int(hexcolor[i:i+2],16)/255) for i in (1,3,5)];return 1.05/(.2126*c[0]+.7152*c[1]+.0722*c[2]+.05)
def theme(industry):
 p=BY_ID[industry];color=p['primary_color'];ratio=contrast(color)
 return {'primary':color,'primary_text':'#FFFFFF' if ratio>=4.5 else '#111827','background':'#F4F6F9','sidebar':'#243447','layout':'classic-admin-sidebar','font':'system-ui','terminology':p['terminology'],'responsive':{'sidebar_breakpoint':768,'max_content_width':1440,'touch_target':44},'contrast_white_on_primary':round(ratio,2),'wcag_certified':False,'full_accessibility_test_required':True}
def configure_theme(value,company,branding=None,components=None):
 branding={} if branding is None else branding;components={} if components is None else components
 require(type(branding) is dict and set(branding)<={'name','logo_path','primary'},'invalid_branding')
 require(type(components) is dict and set(components)<={'radius_px','touch_target'},'invalid_components')
 name=branding.get('name',company);require(type(name) is str and bool(name.strip()) and len(name)<=100 and not any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in name),'invalid_brand_name')
 logo=branding.get('logo_path');require(logo is None or type(logo) is str and re.fullmatch(r'/brand/[A-Za-z0-9][A-Za-z0-9_-]{0,80}\.(?:png|webp|jpg)',logo),'invalid_brand_logo')
 color=branding.get('primary',value['primary']);require(type(color) is str and re.fullmatch(r'#[0-9A-Fa-f]{6}',color),'invalid_brand_color')
 radius=components.get('radius_px',6);touch=components.get('touch_target',44)
 require(type(radius) is int and 0<=radius<=16 and type(touch) is int and 44<=touch<=64,'invalid_component_dimensions')
 ratio=contrast(color);value.update(primary=color,primary_text='#FFFFFF' if ratio>=4.5 else '#111827',contrast_white_on_primary=round(ratio,2),branding={'name':name,'logo_path':logo,'logo_asset_included':False},components={'radius_px':radius,'touch_target':touch})
 value['responsive']['touch_target']=touch
 return value
def plan(spec,company='ERP Perusahaan',industry=None,optional=None,exclude=None,branding=None,components=None):
 require(type(company) is str and 1<=len(company)<=100 and bool(company.strip()) and not any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in company),'invalid_company')
 result=detect(spec,industry)
 if result['clarification_required']:return {'status':'clarification_required','detection':result,'built':False}
 chosen=result['industry'];selected=modules(chosen,optional,exclude);configured=configure_theme(theme(chosen),company,branding,components)
 return {'schema_version':1,'status':'planned','company':company,'detection':result,'industry':chosen,'modules':selected,'theme':configured,'stack':{'frontend':'Next.js','backend':'NestJS','orm':'Prisma7','database':'PostgreSQL'},'implementation_boundary':{'core_runtime':['auth','tenant_scope','roles','customers','products','invoice','verified_manual_payment','balanced_journal','stock_movement','audit','customer_product_revision_crud','quotation_order_fulfillment','purchase_approval_receipt','vendor_verified_manual_payment','ledger_reports','period_close','journal_reversal','monthly_subscription_invoicing','support_ticket_state_machine','paginated_lists','multiline_document_forms','immutable_bom_versions','quality_gated_production','atomic_material_consumption_output','cashier_shift_reconciliation','atomic_cash_sales','admin_full_cash_refunds'],'specialty_modules':'schema_scaffold_only','external_connectors':'unconfigured','deployment':'requires_registered_target_and_explicit_approval','tax_rules':'operator_verified_rates_required; no hardcoded legal rate','sample_customer_data':False},'source_sha256':CATALOG['source_sha256'],'built':False}
PLAN_OPERATIONS={x['operation'] for x in CATALOG['skills'] if x['layer']['number'] in (1,2,3,4)}
AVAILABLE={'industry.analyze.spec','industry.detect.keywords','industry.classify','industry.detect.context','industry.confidence.score','industry.clarify','industry.scale.assess','industry.tier.set','industry.profile.build','industry.region.detect','industry.terminology.extract','module.mapping.industry','module.required.select','module.optional.suggest','module.exclude.irrelevant','module.priority.rank','module.dependency.resolve','module.conflict.detect','module.scale.adjust','module.finalize','theme.color.industry','theme.layout.industry','theme.typography.industry','theme.terminology.apply','theme.responsive.optimize','theme.branding.apply','theme.components.customize','theme.dashboard.layout','theme.navigation.style','schema.entity.identify','schema.relation.design','schema.field.define','schema.data_type.select','schema.constraint.add','schema.index.optimize','schema.audit.column','schema.soft.delete','schema.multi.tenant','schema.migration.generate','schema.er.diagram','schema.document.generate'}
ANALYSIS_AVAILABLE={'finance.depreciation', 'finance.multi.currency', 'finance.journal.entry', 'report.vendor.aging', 'finance.invoice', 'finance.general.ledger', 'inventory.stock.management', 'finance.reconciliation', 'report.customer.aging', 'finance.budgeting'}
ANALYSIS_AVAILABLE |= {'isp.billing.cycle', 'mfg.bom', 'mfg.mrp', 'inventory.stock.opname', 'procurement.three.way.match', 'pos.cash.management', 'pm.time.tracking', 'inventory.batch.tracking', 'mfg.production.costing', 'retail.promotion'}
ANALYSIS_AVAILABLE |= {'mfg.production.scheduling','mfg.routing','mfg.scrap.management','mfg.quality.control'}
ANALYSIS_AVAILABLE |= {'pos.transaction'}
ANALYSIS_AVAILABLE |= {'procurement.rfq','procurement.vendor.performance'}
ANALYSIS_AVAILABLE |= {'report.export.excel','hr.attendance','hr.leave.management','hr.payroll','hr.time.overtime','hr.expense.claim','hr.loan.management'}
ANALYSIS_AVAILABLE |= {'finance.financial.statement','finance.cashflow.statement','report.financial.statement','report.profit.loss','report.cash.flow'}
ANALYSIS_AVAILABLE |= {'pm.milestone','pm.issue.tracking'}
ANALYSIS_AVAILABLE |= {'pm.resource.allocation','pm.risk.management','pm.project.portfolio'}
ANALYSIS_AVAILABLE |= {'crm.pipeline','crm.satisfaction.survey','pm.project.billing','pm.gantt.chart'}
def capabilities():
 rows=[]
 for entry in CATALOG['skills']:
  row=copy.deepcopy(entry);op=row['operation']
  row['status']='input_analysis_available' if op in ANALYSIS_AVAILABLE else 'available' if op in AVAILABLE else 'generator_available' if op in {'builder.scaffold.project','builder.gen.frontend','builder.gen.backend','builder.gen.database','builder.gen.api','builder.gen.auth','builder.gen.dashboard','builder.gen.menu','builder.gen.workflow','builder.gen.report'} else 'connector_required' if op.startswith(('connector.','comm.','integration.')) else 'not_implemented'
  row['readiness_means']='caller_supplied_data_calculation_no_posting' if row['status']=='input_analysis_available' else 'planning_or_source_generation_only' if row['status'] in ('available','generator_available') else 'not_callable'
  rows.append(row)
 return {'schema_version':1,'catalog_size':len(rows),'industry_profiles':len(PROFILES),'skills':rows,'source_counts_reconciled':True,'all_skills_operational':False,'callable_entrypoints':len(AVAILABLE|ANALYSIS_AVAILABLE)+13,'orchestrators':['erp_all.plan','erp_all.generate','erp_all.catalog']}
