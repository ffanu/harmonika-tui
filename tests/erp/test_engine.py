import base64,hashlib,io,json,sys,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from erp_engine.planner import CATALOG,PROFILES,AVAILABLE,ERPError,detect,modules,plan,capabilities
from erp_engine.generator import generate
from erp_engine.schema import generate_schema
from erp_engine.operations import invoke
class Engine(unittest.TestCase):
 def test_full_source_reconciled(self):
  self.assertEqual(CATALOG['actual_skills'],363);self.assertEqual(len(PROFILES),25);self.assertEqual(CATALOG['source_sha256'],hashlib.sha256((ROOT/'backend/erp_engine/specification.txt').read_bytes()).hexdigest())
 def test_each_number_preserved(self):self.assertEqual([r['number'] for r in CATALOG['skills']],list(range(1,364)))
 def test_malformed_source_ids_have_explicit_normalization(self):
  row=next(r for r in CATALOG['skills'] if r['source_id']=='finance.tax.e faktur');self.assertEqual(row['operation'],'finance.tax.efaktur')
 def test_isp_detection(self):self.assertEqual(detect('Saya punya ISP, 500 pelanggan internet paket 20Mbps')['industry'],'isp')
 def test_ambiguous_needs_clarification(self):self.assertTrue(detect('Saya membutuhkan sistem perusahaan')['clarification_required'])
 def test_negation(self):self.assertEqual(detect('Bukan ISP, saya punya toko retail')['industry'],'retail')
 def test_explicit_industry_is_validated(self):
  with self.assertRaises(ERPError):detect('usaha','not-an-industry')
 def test_scale_is_not_legal_claim(self):self.assertEqual(detect('ISP 500 pelanggan')['scale'],'small')
 def test_dependencies_topological(self):
  rows=modules('isp');ids=[r['id']for r in rows]
  for i,row in enumerate(rows):
   for dep in row['dependencies']:self.assertLess(ids.index(dep),i)
 def test_required_dependencies_not_mislabeled_optional(self):
  sales=next(r for r in modules("isp") if r["id"]=="sales");self.assertTrue(sales["required"]);self.assertEqual(sales["selection_reason"],"required_dependency")
 def test_required_module_cannot_be_excluded(self):
  with self.assertRaises(ERPError):modules('retail',exclude=['inventory'])
 def test_irrelevant_domains_not_selected(self):self.assertNotIn('healthcare',[r['id']for r in modules('isp')])
 def test_all_profiles_generate_valid_kits(self):
  for profile in PROFILES:
   with self.subTest(industry=profile['id']):
    output=generate('Perusahaan uji','Synthetic QA',profile['id']);self.assertFalse(output['deployed']);self.assertFalse(output['tests_run']);self.assertIn('apps/api/src/auth.ts',output['files']);self.assertIn('apps/web/app/page.tsx',output['files']);self.assertEqual(output['plan']['industry'],profile['id']);self.assertLess(len(json.dumps({k:v for k,v in output.items() if k!='files'}).encode()),262144)
 def test_no_default_owner_password(self):
  output=generate('toko retail','QA','retail');self.assertNotIn('OWNER_PASSWORD=admin',json.dumps(output));self.assertIn('OWNER_PASSWORD=\n',output['files']['.env.example'])
 def test_no_implicit_legal_tax_rate(self):self.assertNotIn('PPN 11%',json.dumps(generate('ISP','QA','isp')['files']))
 def test_schema_forces_rls_and_composite_foreign_keys(self):
  p=plan('ISP','QA','isp');s=generate_schema(p);self.assertIn('FORCE ROW LEVEL SECURITY',s['tenant_policies_sql']);self.assertIn('references:[tenantId,id]',s['prisma']);self.assertIn('set_config',(ROOT/'backend/erp_engine/templates/apps/api/src/db.ts').read_text())
 def test_artifact_integrity_and_paths(self):
  output=generate('toko','QA','retail');raw=base64.b64decode(output['artifact']['zip_base64']);self.assertEqual(hashlib.sha256(raw).hexdigest(),output['artifact']['sha256'])
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   for n in z.namelist():self.assertFalse(n.startswith('/'));self.assertNotIn('..',Path(n).parts)
 def test_company_cannot_inject_source(self):
  v=generate('ISP','</script>";deleteAll();//','isp');parsed=json.loads(v['files']['apps/web/app/plan.json']);self.assertEqual(parsed['company'],'</script>";deleteAll();//')
 def test_unknown_fields_fail(self):
  with self.assertRaises(ERPError):invoke('plan',{'spec':'ISP','secret':'private'})
 def test_capability_does_not_claim_all_operational(self):
  c=capabilities();self.assertFalse(c['all_skills_operational']);self.assertEqual(len(c['skills']),363)
 def test_unimplemented_skill_refuses_not_generic_success(self):
  with self.assertRaises(ERPError):invoke('healthcare.medical.record',{'spec':'klinik'})
 def test_finance_input_analysis_only(self):
  v=invoke('finance.journal.entry',{'lines':[{'account':'cash','debit_minor':'100','credit_minor':'0'},{'account':'revenue','debit_minor':'0','credit_minor':'100'}]});self.assertTrue(v['result']['balanced']);self.assertFalse(v['posted'])
 def test_unbalanced_journal_rejected(self):
  with self.assertRaises(ERPError):invoke('finance.journal.entry',{'lines':[{'account':'cash','debit_minor':'100','credit_minor':'0'},{'account':'revenue','debit_minor':'0','credit_minor':'90'}]})
 def test_stock_cannot_go_negative(self):
  with self.assertRaises(ERPError):invoke('inventory.stock.management',{'initial':2,'movements':[-3]})
 def test_invoice_uses_integer_money(self):
  self.assertEqual(invoke('finance.invoice',{'lines':[{'unit_minor':'100000000000000001','quantity':2}],'tax_bps':0})['result']['total_minor'],'200000000000000002')
 def test_float_money_rejected(self):
  with self.assertRaises(ERPError):invoke('finance.invoice',{'lines':[{'unit_minor':1.1,'quantity':1}],'tax_bps':0})
 def test_available_operation_dispatch(self):
  for operation in AVAILABLE:
   with self.subTest(operation=operation):self.assertIn('status',invoke(operation,{'spec':'ISP','company':'QA','industry':'isp'}))
if __name__=='__main__':unittest.main()
