"""Public worker boundary never accepts a client workspace or privilege."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_worker', ROOT/'backend/auto-skills/public_skill_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

class PublicWorkerBoundaryTests(unittest.TestCase):
    def body(self, **extra):
        return dict(skill_id='seo.page_audit', input={'url':'https://example.com/'}, principal_id='api-token:1', **extra)
    def test_valid_identity(self):
        self.assertEqual(worker.validate(self.body())['principal_id'], 'api-token:1')
    def test_no_workspace(self):
        with self.assertRaises(ValueError):worker.validate(self.body(project_root='/etc'))
    def test_no_secret_fields(self):
        with self.assertRaises(ValueError):worker.validate(self.body(token='not-a-real-key'))
    def test_local_actor_is_not_public_actor(self):
        body=self.body();body['principal_id']='local:0'
        with self.assertRaises(ValueError):worker.validate(body)
    def test_mutating_or_arbitrary_operations_denied(self):
        for skill in ('seo.visual','seo.drift','shell.exec','file.read','doc.create_pdf','../../shell'):
            with self.subTest(skill=skill):
                body=self.body();body['skill_id']=skill
                with self.assertRaises(ValueError):worker.validate(body)
    def test_zero_actor(self):
        body=self.body();body['principal_id']='api-token:0'
        with self.assertRaises(ValueError):worker.validate(body)
    def test_no_array_arguments(self):
        body=self.body();body['input']=[]
        with self.assertRaises(ValueError):worker.validate(body)
    def test_catalog_matches_fixed_allowlist(self):
        catalog=json.loads((ROOT/'backend/auto-skills/catalog.json').read_text())
        names={tool['function']['name'].replace('_','.',1) for tool in catalog}
        self.assertEqual(names,worker.ALLOWED)
        for tool in catalog:
            props=tool['function']['parameters'].get('properties',{})
            self.assertFalse({'output_dir','baseline_path','principal_id','project_root'} & set(props))

if __name__=='__main__':unittest.main()
