import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/tools'))
from skill_registry import Registry,SkillError

class SEORegistryTests(unittest.TestCase):
    def setUp(self):self.registry=Registry()
    def test_fourteen_seo_operations_and_no_raw_shell(self):
        items=[item for item in self.registry.list() if item.get('worker')=='seo']
        self.assertEqual(len(items),14)
        for item in items:
            self.assertTrue(item['permissions']['network']);self.assertFalse(item['permissions']['shell'])
            self.assertEqual(item['worker'],'seo')
            self.assertNotIn('available',item)
        self.assertEqual(len([v for v in self.registry.list() if v['id'].startswith('doc.')]),9)
    def test_all_valid_input_shapes(self):
        for name in ('page_audit','technical','content','schema','sitemap','local','geo','performance','backlinks'):
            self.registry.validate_input('seo.'+name,{'url':'https://example.com/'})
        self.registry.validate_input('seo.visual',{'url':'https://example.com/','output_dir':'/project/screens'})
        self.registry.validate_input('seo.drift',{'url':'https://example.com/','action':'capture','baseline_path':'/project/base.json'})
        self.registry.validate_input('seo.keyword_cluster',{'keywords':['internet rumah','paket internet'],'serp_results':{}})
        for name in ('search_console','ga4'):
            self.registry.validate_input('seo.'+name,{'property_id':'1234567890','start_date':'2026-10-01','end_date':'2026-10-20','limit':100})
    def test_credential_and_identity_injection_rejected(self):
        for field in ('principal_id','Authorization','api_key','google_config','project_root','headers'):
            with self.assertRaises(SkillError):self.registry.validate_input('seo.page_audit',{'url':'https://example.com/',field:'untrusted'})
    def test_bounds_and_effects(self):
        for payload in ({'keywords':[]},{'keywords':['x']*51},{'keywords':'not-list'}):
            with self.assertRaises(SkillError):self.registry.validate_input('seo.keyword_cluster',payload)
        with self.assertRaises(SkillError):self.registry.validate_input('seo.backlinks',{'url':'https://example.com/','links':['https://example.com/']*11})
        with self.assertRaises(SkillError):self.registry.validate_input('seo.drift',{'url':'https://example.com/','action':'delete','baseline_path':'/project/base.json'})
        with self.assertRaises(SkillError):self.registry.validate_input('seo.drift',{'url':'https://example.com/','action':'capture','baseline_path':'/project/private.pdf'})
        self.assertEqual(self.registry.get('seo.drift')['write_condition'],{'field':'action','equals':'capture'})
        self.assertEqual(self.registry.get('seo.visual')['output_field'],'output_dir')

if __name__=='__main__':unittest.main(verbosity=2)
