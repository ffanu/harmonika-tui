import hashlib,json,unittest
from pathlib import Path
from backend.mobile_engine.registry import descriptors,dispatch,CANONICAL,capabilities
from backend.mobile_engine import generators as g
class SourceContract(unittest.TestCase):
 def test_every_available_skill_and_advertised_framework(self):
  for row in descriptors():
   if row['status']!='available':continue
   variants=[{'spec':'Android dan iOS, katalog offline.'}] if row['mode']=='planning' else [{}]
   if row['mode']=='source_generation':variants.extend({'framework':fw} for fw in row['input_schema'].get('properties',{}).get('framework',{}).get('enum',[]))
   for v in variants:
    out=dispatch(row['id'],v)
    self.assertEqual(out['mode'],row['mode'],row['id'])
    self.assertFalse(out['build_performed']);self.assertFalse(out['runtime_verified'])
    self.assertLessEqual(len(json.dumps(out,ensure_ascii=False).encode()),262144)
    if row['mode']=='source_generation':self.assertTrue(out['files'])
 def test_schema_framework_default_matches_real_handler(self):
  for id,desc in g.descriptors().items():
   if not g.support(id):continue
   prop=desc['input_schema']['properties']['framework'];self.assertIn(prop['default'],prop['enum'])
   self.assertEqual(g.dispatch(id,{})['status'],'completed')
 def test_injection_and_reserved_identifier_rejected(self):
  for value in ('{global.process.exit()}','PT $x','Toko \\(pwn)','Toko & Sons',"Toko';exec",'A\nB'):
   for id in ('scaffold.expo','scaffold.flutter','scaffold.kotlin_android','scaffold.swift_ios','scaffold.ionic'):
    with self.assertRaises(ValueError):dispatch(id,{'name':value})
  for value in ('id.class.app','id.when.app','id.my_app.test'):
   with self.assertRaises(ValueError):dispatch('scaffold.kotlin_android',{'identifier':value})
  for value in ("https://a.invalid/x';evil()",'https://a.invalid/$(touch)', 'https://user:pass@a.invalid','http://a.invalid','https://a.invalid:99999'):
   with self.assertRaises(ValueError):dispatch('net.setup.fetch',{'api_base_url':value})
 def test_flutters_reserved_app_name_and_scaffold_settings(self):
  out=dispatch('scaffold.flutter',{'name':'Class'});files={x['path']:x['content'] for x in out['files']};self.assertIn('name: app_class',files['pubspec.yaml']);self.assertNotIn('include: package:flutter/',files['analysis_options.yaml'])
  files={x['path']:x['content'] for x in dispatch('scaffold.swift_ios',{})['files']};self.assertIn('SWIFT_VERSION: "5.0"',files['project.yml'])
 def test_no_fictitious_or_wrong_domain_aliases(self):
  for id in g.descriptors():self.assertIn(id,CANONICAL)
  self.assertFalse(any(r['id']=='scaffold.react_native_cli' and r['status']=='available' for r in descriptors()))
 def test_expo_baseline_and_native_type_fixes(self):
  files={x['path']:x['content'] for x in dispatch('scaffold.expo',{})['files']};p=json.loads(files['package.json'])
  self.assertEqual(p['dependencies']['expo'],'58.0.19');self.assertEqual(p['dependencies']['react-native'],'0.88.0')
  self.assertIn('[K in keyof typeof palette.light]: string',files['src/theme/theme.ts'])
 def test_actual_ionic_core_used_and_platform_deps(self):
  f={x['path']:x['content'] for x in dispatch('scaffold.ionic',{})['files']};p=json.loads(f['package.json'])
  for name in ('@ionic/core','@capacitor/android','@capacitor/ios'):self.assertIn(name,p['dependencies'])
  self.assertIn('defineCustomElements',f['src/main.ts']);self.assertIn('<ion-app>',f['index.html'])
 def test_source_network_does_not_attach_auth_to_arbitrary_origin(self):
  for id in ('net.setup.axios','net.setup.fetch'):
   code='\n'.join(x['content'] for x in dispatch(id,{})['files'])
   self.assertIn('Unsafe API request target',code)
 def test_spec_source_immutable(self):
  root=Path(__file__).resolve().parents[1]/'backend/mobile_engine'
  manifest=json.loads((root/'catalog_source.json').read_text());self.assertEqual(hashlib.sha256((root/'specification.txt').read_bytes()).hexdigest(),manifest['source_sha256'])
if __name__=='__main__':unittest.main()
