import base64,json,struct,time,unittest
from backend.master_engine.analyzers import dispatch
from backend.master_engine.adapters import descriptors
class Regressions(unittest.TestCase):
 def test_email_and_domain_hostile_long_text_is_bounded(self):
  started=time.monotonic()
  for id,text in [('security.pii.mask','a'*120000),('malware.ioc.extract','a.'*50000+'x')]:
   out=dispatch(id,{'text':text});self.assertEqual(out['status'],'completed')
  self.assertLess(time.monotonic()-started,2)
 def test_ioc_types_not_mixed_and_every_address_defanged(self):
  out=dispatch('malware.ioc.extract',{'text':'https://evil.test a@evil.test 1.2.3.4 other.test 999.1.2.3 \x009999\x00'});self.assertEqual(out['status'],'completed');d=out['data'];self.assertIn('1[.]2[.]3[.]4',d['ips']);self.assertIn('other[.]test',d['domains']);self.assertIn('a[@]evil[.]test',d['defanged_text']);self.assertIn('other[.]test',d['defanged_text']);self.assertNotIn('999[.]1[.]2[.]3',d['ips'])
 def test_corrupt_binary_header_reads_report_findings(self):
  pe=bytearray(88);pe[:2]=b'MZ';struct.pack_into('<I',pe,60,64);pe[64:68]=b'PE\0\0';struct.pack_into('<H',pe,84,70)
  for id,data in [('binary.inspect.pe',pe),('binary.inspect.elf',b'\x7fELF'+bytes([2,1,1])+bytes(49)),('binary.inspect.elf',b'\x7fELF'+bytes([2,1,1])+bytes(9)),('binary.inspect.macho',bytes.fromhex('cffaedfe'))]:
   out=dispatch(id,{'data_base64':base64.b64encode(data).decode()});self.assertEqual(out['status'],'completed',out);self.assertTrue(out['findings'])
 def test_macho_dylib_type(self):
  data=struct.pack('<IiiIIIII',0xfeedfacf,0x1000007,3,6,0,0,0,0);out=dispatch('binary.inspect.macho',{'data_base64':base64.b64encode(data).decode()});self.assertEqual(out['data']['filetype'],'dylib')
 def test_count_and_amount_windows_are_independent(self):
  events=[{'timestamp':'2026-01-01T00:00:0'+str(i)+'Z','amount':1} for i in range(3)]+[{'timestamp':'2026-01-01T00:10:0'+str(i)+'Z','amount':10000} for i in range(2)]
  out=dispatch('payment.velocity.check',{'events':events,'window_seconds':5,'threshold':3});self.assertEqual(out['metrics']['max_events_in_window'],3);self.assertEqual(out['metrics']['max_amount_in_window'],20000)
  for n in (-1,1e308,float('inf')):self.assertEqual(dispatch('payment.velocity.check',{'events':[{'timestamp':'2026-01-01T00:00:00Z','amount':n}],'window_seconds':5,'threshold':3})['status'],'rejected')
 def test_duplication_counts_union_not_overlapping_windows(self):
  out=dispatch('static.duplication',{'code':('a\nb\nc\nd\ne\nf\n')*10});self.assertLessEqual(out['metrics']['duplication_ratio'],1);self.assertLessEqual(out['metrics']['duplicated_lines_min_estimate'],60)
 def test_sort_fractional_seconds_by_time_not_string(self):
  out=dispatch('sort.by_date.asc',{'records':[{'id':'CVE-2026-1234','published':'2026-01-01T00:00:00Z'},{'id':'CVE-2026-1235','published':'2026-01-01T00:00:00.1Z'}]});self.assertEqual(out['data']['records'][0]['id'],'CVE-2026-1234')
 def test_public_schemas_are_jsonschema_objects(self):
  for id,item in descriptors().items():self.assertEqual(item['input_schema']['type'],'object',id);self.assertFalse(item['input_schema']['additionalProperties'])

class BinaryBoundaries(unittest.TestCase):
 def test_all_truncation_lengths_are_findings_not_parser_exceptions(self):
  samples={'binary.inspect.pe':bytearray(160),'binary.inspect.elf':bytearray(80),'binary.inspect.macho':bytearray(80)}
  pe=samples['binary.inspect.pe'];pe[:2]=b'MZ';struct.pack_into('<I',pe,60,64);pe[64:68]=b'PE\0\0';struct.pack_into('<HHIIIHH',pe,68,0x8664,1,0,0,0,70,0)
  elf=samples['binary.inspect.elf'];elf[:7]=b'\x7fELF\x02\x01\x01'
  mach=samples['binary.inspect.macho'];struct.pack_into('<IiiIIIII',mach,0,0xfeedfacf,0x1000007,0,6,1,48,0,0);struct.pack_into('<II',mach,32,12,8)
  for id,full in samples.items():
   for n in range(len(full)+1):
    out=dispatch(id,{'data_base64':base64.b64encode(full[:n]).decode()});self.assertEqual(out['status'],'completed',(id,n,out))

class NestedAndCSP(unittest.TestCase):
 def test_nested_required_fields_are_not_invented(self):
  out=dispatch('behavior.velocity',{'events':[{'timestamp':'2026-01-01T00:00:00Z'}],'window_seconds':5,'threshold':1});self.assertEqual(out['status'],'rejected')
 def test_csp_duplicate_preserves_first_unsafe_directive(self):
  out=dispatch('web.csp.test',{'csp':"default-src 'self'; script-src *; script-src 'none'; object-src 'none'; frame-ancestors 'none'"});self.assertTrue(out['metrics']['weak_config_detected']);self.assertTrue(any(x['id']=='csp.duplicate_directive_ignored' for x in out['findings']))
