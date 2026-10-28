"""Qualification of every active pure ERP contract, not just registry counts."""
import base64,hashlib,importlib.util,io,json,sys,tempfile,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from erp_engine.operations import invoke,ANALYSIS_OPS,GENERATOR_OPS
from erp_engine.planner import AVAILABLE,ERPError
from engine_registry.discovery import Discovery
s=importlib.util.spec_from_file_location('matrix_bundle',ROOT/'scripts/bundle-erp-plugins.py');b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
FIXTURES=json.loads((ROOT/'tests/erp/fixtures/active-contracts.json').read_text())
def cases():
 result={op:{'spec':'ERP jasa proyek','company':'Synthetic QA','industry':'service','optional':['projects']} for op in AVAILABLE|GENERATOR_OPS|{'plan','generate'}}
 result.update({op:{'data':data} for op,data in FIXTURES.items()});result['catalog']={};return result

def check_output(op,value):
 assert type(value) is dict and 'error' not in value,op
 if op in ANALYSIS_OPS:
  assert value['status']=='analyzed_input' and value['scope']=='caller_supplied_data_only'
  assert value['posted'] is False and value['live_database_access'] is False
 elif op=='catalog':assert value['catalog_size']==363 and not value['all_skills_operational']
 elif op in GENERATOR_OPS|{'generate'}:
  assert not value['built'] and not value['deployed'] and not value['tests_run']
  artifact=value['artifact'];raw=base64.b64decode(artifact['zip_base64'],validate=True)
  assert hashlib.sha256(raw).hexdigest()==artifact['sha256']
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   manifest=json.loads(z.read('source-manifest.json'))
   assert set(z.namelist())==set(manifest)|{'source-manifest.json'}
   for name,digest in manifest.items():assert hashlib.sha256(z.read(name)).hexdigest()==digest
 elif op.startswith('schema.'):
  assert value['migration_executed'] is False and 'FORCE ROW LEVEL SECURITY' in value['result']['tenant_policies_sql']
 return value

class Matrix(unittest.IsolatedAsyncioTestCase):
 async def test_every_active_entrypoint_executes_reviewed_contract(self):
  self.assertEqual(set(FIXTURES),ANALYSIS_OPS);rows=cases();self.assertEqual(len(rows),102)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();bundle=b.bundle(root/'plugins');self.assertEqual(bundle['plugins'],len(rows));d=Discovery(root/'plugins',root/'isolated.sqlite',reserved_ids=[]);await d.refresh()
   self.assertEqual(d.summary()['registered'],len(rows))
   for op,payload in sorted(rows.items()):
    with self.subTest(operation=op):
     output=check_output(op,await d.execute('erp_all.'+op,payload))
     expected=invoke(op,payload['data'] if op in ANALYSIS_OPS else payload)
     if 'files' in expected:expected['source_file_count']=len(expected.pop('files'))
     self.assertEqual(output,expected)

class InvalidContracts(unittest.TestCase):
 def test_each_active_operation_rejects_unknown_input(self):
  for op in cases():
   with self.subTest(operation=op),self.assertRaises(ERPError):invoke(op,{'__unexpected':True})
 def test_malformed_module_choices_never_escape_as_python_errors(self):
  for k in ['optional','exclude']:
   for bad in [False,0,{},'hr',[{}],[True],['hr','hr'],['hr']*41]:
    with self.subTest(key=k,value=bad),self.assertRaises(ERPError):invoke('plan',{'spec':'ISP','industry':'isp',k:bad})
  with self.assertRaisesRegex(ERPError,'module_selection_conflict'):invoke('plan',{'spec':'ERP jasa','industry':'service','optional':['hr'],'exclude':['hr']})
 def test_required_spec_and_typed_industry(self):
  for data in [{},{'spec':' '},{'spec':'ISP','industry':[]},{'spec':'ISP','company':' '},{'spec':'ISP','company':'\ud800'}]:
   with self.subTest(data=data),self.assertRaises(ERPError):invoke('plan',data)
 def test_all_entrypoints_enforce_global_firewall_bounds(self):
  deep={};cursor=deep
  for _ in range(18):cursor['x']={};cursor=cursor['x']
  for data,code in [(deep,'input_complexity_limit'),({'x':'a'*1048576},'input_too_large'),({'x':float('nan')},'invalid_json_number'),({'x':'\ud800'},'invalid_unicode'),({'x':2**300},'input_integer_limit'),({1:'a'},'invalid_json_key')]:
   for op in (['catalog','plan','builder.gen.api'] if code=='invalid_unicode' else ['catalog','plan','finance.invoice','builder.gen.api']):
    with self.subTest(operation=op,code=code),self.assertRaisesRegex(ERPError,code):invoke(op,data)

class NamedOperations(unittest.TestCase):
 def args(self,**kw):return {'spec':'ISP 500 pelanggan','industry':'isp',**kw}
 def focus(self,op,**kw):return invoke(op,self.args(**kw))['operation_result']['data']
 def test_all41_named_planning_results_are_explicit(self):
  for op in AVAILABLE:
   with self.subTest(operation=op):
    r=invoke(op,self.args());self.assertEqual(r['operation_result']['operation'],op);self.assertFalse(r['operation_result']['live_business_mutation']);self.assertTrue(r['operation_result']['data'])
 def test_suggestions_priority_conflict_and_finalization_are_distinct(self):
  self.assertIn('hr',[r['id'] for r in self.focus('module.optional.suggest')['suggestions']])
  self.assertTrue(all(r['required'] for r in self.focus('module.required.select')['modules']))
  ranks=self.focus('module.priority.rank')['ranking'];self.assertEqual([r['priority'] for r in ranks],list(range(1,len(ranks)+1)))
  self.assertEqual(self.focus('module.conflict.detect')['conflicts'],[])
  self.assertIn('modules',self.focus('module.finalize'))
 def test_branding_and_components_are_validated_and_used_by_source(self):
  v=invoke('generate',self.args(branding={'name':'Brand QA','primary':'#ffffff','logo_path':'/brand/logo.png'},components={'radius_px':0,'touch_target':48}))
  p=v['plan'];self.assertEqual(p['theme']['branding']['name'],'Brand QA');self.assertEqual(p['theme']['primary_text'],'#111827');self.assertEqual(p['theme']['components']['touch_target'],48)
  self.assertIn('plan.theme.branding.logo_path',v['files']['apps/web/app/page.tsx']);self.assertFalse(p['theme']['branding']['logo_asset_included'])
  for branding,components in [({'logo_path':'https://evil.invalid/logo.png'},{}),({'primary':'red;evil'},{}),({'name':'\n'},{}),({}, {'radius_px':False}),({}, {'touch_target':20}),({'unknown':'x'}, {})]:
   with self.subTest(branding=branding,components=components),self.assertRaises(ERPError):invoke('plan',self.args(branding=branding,components=components))
 def test_index_design_covers_actual_composite_foreign_keys_without_redundant_unique(self):
  from erp_engine.schema import foreign_key_indexes
  self.assertEqual(foreign_key_indexes('x X @relation(fields:[tenantId,xId],references:[tenantId,id])\n@@unique([tenantId,xId])'),[])
  s=invoke('schema.index.optimize',self.args());self.assertIn('@@index([tenantId,customerId])',s['result']['prisma']);self.assertFalse(s['operation_result']['data']['performance_measured'])
 def test_journal_overflow_is_rejected_like_invoice(self):
  lines=[]
  for i in range(10):lines.extend([{'account':'d'+str(i),'debit_minor':'999999999999999999','credit_minor':'0'},{'account':'c'+str(i),'debit_minor':'0','credit_minor':'999999999999999999'}])
  for op in ['finance.journal.entry','finance.general.ledger']:
   with self.subTest(operation=op),self.assertRaisesRegex(ERPError,'amount_exceeds_storage_limit'):invoke(op,{'lines':lines})
 def test_catalog_reconciles_without_modifying_blueprint_rows(self):
  c=invoke('catalog',{});self.assertEqual(len(c['skills']),363);self.assertEqual(c['callable_entrypoints'],102);self.assertEqual(len(c['orchestrators']),3)
