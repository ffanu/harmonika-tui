import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wan14_guidance_tests', ROOT/'backend/wan14/guided_workflow.py')
wf = importlib.util.module_from_spec(spec); spec.loader.exec_module(wf)


class GuidanceTests(unittest.TestCase):
    def test_explicit_exclusions_have_effective_negative_guidance(self):
        graph = wf.build(wf.FAMILY, 'A stationary router. No people. No hands. No fingers.', 1, 'a'*32, 'ref.png')
        self.assertIn('people, person', graph['4']['inputs']['text'])
        self.assertNotIn('No hands', graph['3']['inputs']['text'])
        for key in ('8', '9'):
            self.assertEqual(graph[key]['inputs']['cfg'], 3.5)
            self.assertEqual(graph[key]['inputs']['steps'], 20)
        self.assertEqual(graph['8']['inputs']['end_at_step'], 10)
        self.assertEqual(graph['9']['inputs']['start_at_step'], 10)
        self.assertEqual(graph['9']['inputs']['end_at_step'], 20)
        self.assertFalse(any(n['class_type'] == 'LoraLoaderModelOnly' for n in graph.values()))

    def test_people_requested_by_user_are_not_globally_blocked(self):
        graph = wf.build(wf.FAMILY, 'A woman waves to camera.', 1, 'a'*32, 'ref.png')
        self.assertNotIn('people, person', graph['4']['inputs']['text'])
        graph = wf.build(wf.FAMILY, 'A woman portrait. No hands.', 1, 'a'*32, 'ref.png')
        self.assertNotIn('people, person', graph['4']['inputs']['text'])
        self.assertIn('hands, fingers', graph['4']['inputs']['text'])

    def test_text_qualification_is_preserved_when_only_reference_preset_changes(self):
        family = 'wan22_t2v_14b_lightx2v'
        self.assertEqual(wf.workflow_hash_for_family(family), wf.base.workflow_hash_for_family(family))
        self.assertEqual(wf.build(family, 'fixture', 5, 'a'*32), wf.base.build(family, 'fixture', 5, 'a'*32))
        self.assertNotEqual(wf.workflow_hash_for_family(wf.FAMILY), wf.base.workflow_hash_for_family('wan22_i2v_14b'))


if __name__ == '__main__':
    unittest.main()
