import base64,hashlib,importlib.util,io,os,stat,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from erp_engine.operations import invoke
from erp_engine.planner import ERPError
s=importlib.util.spec_from_file_location('erp_export_cli',ROOT/'runtime/tools/erp_cli.py');cli=importlib.util.module_from_spec(s);s.loader.exec_module(cli)
class Export(unittest.TestCase):
 def payload(self):return {'title':'R&D <Harmonika>','headers':['ID','Note'],'rows':[['000123','=HYPERLINK("https://example.invalid")'],['999999999999999999','A&B <text>']]}
 def result(self):return invoke('report.export.excel',self.payload())['result']
 def mutate(self,result,name,transform):
  artifact=result['artifact'];data=base64.b64decode(artifact['base64']);out=io.BytesIO()
  with zipfile.ZipFile(io.BytesIO(data)) as source,zipfile.ZipFile(out,'w') as target:
   for member in source.namelist():target.writestr(member,transform(source.read(member)) if member==name else source.read(member))
  artifact.update(base64=base64.b64encode(out.getvalue()).decode(),sha256=hashlib.sha256(out.getvalue()).hexdigest());return result
 def test_real_xlsx_keeps_identifiers_and_formulas_as_text(self):
  r=self.result();data=base64.b64decode(r['artifact']['base64']);self.assertEqual(hashlib.sha256(data).hexdigest(),r['artifact']['sha256']);self.assertFalse(r['formulas_enabled'])
  with zipfile.ZipFile(io.BytesIO(data)) as z:
   self.assertEqual(len(z.namelist()),7);sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'));ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'};cells=sheet.findall('.//m:c',ns);self.assertTrue(all(c.get('t')=='inlineStr' for c in cells));self.assertEqual(cells[2].find('m:is/m:t',ns).text,'000123');self.assertTrue(cells[3].find('m:is/m:t',ns).text.startswith('=HYPERLINK'));self.assertEqual(sheet.findall('.//m:f',ns),[]);self.assertIn(b'&lt;Harmonika&gt;',z.read('docProps/core.xml'))
 def test_invalid_shapes_types_duplicates_and_xml_are_rejected(self):
  for payload,code in [({**self.payload(),'headers':['x','x']},'duplicate_header'),({**self.payload(),'rows':[['x']]},'invalid_export_row'),({**self.payload(),'rows':[[True,'x']]},'text_cells_required'),({**self.payload(),'rows':[['x','\x00']]},'invalid_xml_text'),({**self.payload(),'rows':[['x','y']]*1001},'invalid_rows')]:
   with self.assertRaisesRegex(ERPError,code):invoke('report.export.excel',payload)
 def test_client_saves_verified_private_file_and_does_not_return_base64(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'HARMONIKA_DOWNLOADS_DIR':str(Path(temp).resolve())}):
   r=cli.save_excel(self.result());p=Path(r['artifact_path']);self.assertEqual(p.suffix,'.xlsx');self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600);self.assertNotIn('base64',r);self.assertFalse(r['business_data_updated'])
 def test_client_rejects_bad_hash(self):
  r=self.result();r['artifact']['sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'artifact_integrity_failed'):cli.save_excel(r)
 def test_client_rejects_formula_nodes_even_with_valid_hash(self):
  r=self.mutate(self.result(),'xl/worksheets/sheet1.xml',lambda b:b.replace(b'</c>',b'<f>1+1</f></c>',1))
  with self.assertRaisesRegex(ValueError,'unsafe_report_xml'):cli.save_excel(r)
 def test_client_rejects_external_relationships(self):
  r=self.mutate(self.result(),'_rels/.rels',lambda b:b.replace(b'Target="xl/workbook.xml"',b'Target="https://example.invalid/evil" TargetMode="External"'))
  with self.assertRaisesRegex(ValueError,'unsafe_report_xml'):cli.save_excel(r)
 def test_client_rejects_xml_entities(self):
  r=self.mutate(self.result(),'docProps/core.xml',lambda b:b'<!DOCTYPE evil [<!ENTITY e "x">]>'+b)
  with self.assertRaisesRegex(ValueError,'unsafe_report_xml'):cli.save_excel(r)
 def test_client_refuses_symlink_output_directory(self):
  with tempfile.TemporaryDirectory() as temp:
   base=Path(temp).resolve();(base/'actual').mkdir();(base/'alias').symlink_to(base/'actual',target_is_directory=True)
   with patch.dict(os.environ,{'HARMONIKA_DOWNLOADS_DIR':str(base/'alias')}):
    with self.assertRaisesRegex(ValueError,'unsafe_artifact_directory'):cli.save_excel(self.result())

 def test_client_rejects_external_target_even_without_targetmode(self):
  r=self.mutate(self.result(),'_rels/.rels',lambda b:b.replace(b'Target="xl/workbook.xml"',b'Target="https://example.invalid/evil"'))
  with self.assertRaisesRegex(ValueError,'unsafe_report_xml'):cli.save_excel(r)

 def test_invalid_unicode_is_rejected_as_input(self):
  v=self.payload();v['rows']=[['x','\ud800']]
  with self.assertRaisesRegex(ERPError,'text_cells_required'):invoke('report.export.excel',v)

 def test_server_and_client_share_uncompressed_package_limit(self):
  v={'title':'QA','headers':['h'+str(n) for n in range(30)],'rows':[['&'*13]*30 for _ in range(333)]}
  with self.assertRaisesRegex(ERPError,'export_uncompressed_limit'):invoke('report.export.excel',v)
