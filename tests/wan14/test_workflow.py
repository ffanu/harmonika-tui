import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wan14_test_workflow', ROOT/'backend/wan14/workflow.py')
wf = importlib.util.module_from_spec(spec); spec.loader.exec_module(wf)


class Full14Tests(unittest.TestCase):
    def test_accelerated_experts_have_matching_loras_and_continuous_noise_schedule(self):
        for kind in ('t2v', 'i2v'):
            family = f'wan22_{kind}_14b_lightx2v'
            graph = wf.build(family, 'fixture', 5, 'a'*32, 'ref.png' if kind == 'i2v' else None)
            for key, noise in [('5', 'high'), ('6', 'low')]:
                self.assertIn(f'wan2.2_{kind}_{noise}', graph[key]['inputs']['unet_name'])
                self.assertIn(f'wan2.2_{kind}_lightx2v_', graph[key+'l']['inputs']['lora_name'])
                self.assertIn(f'_{noise}_noise.', graph[key+'l']['inputs']['lora_name'])
                self.assertEqual(graph[key+'s']['inputs']['model'], [key+'l', 0])
            self.assertEqual(graph['8']['inputs']['end_at_step'], graph['9']['inputs']['start_at_step'])
            self.assertEqual(graph['9']['inputs']['end_at_step'], 4)
            self.assertEqual(graph['9']['inputs']['steps'], wf.profile(family)['steps'])
            self.assertEqual(graph['9']['inputs']['cfg'], 1.0)
            self.assertEqual(graph['9']['inputs']['add_noise'], 'disable')
            self.assertEqual(graph['8']['inputs']['latent_image'], ['7', 2 if kind == 'i2v' else 0])
            self.assertNotEqual(wf.workflow_hash_for_family(family), wf.base.workflow_hash())

    def test_text_graph_is_identical_to_frozen_base_and_uses_its_actual_proof(self):
        for seconds in (1, 5, 10):
            self.assertEqual(wf.build('wan22_14b', 'fixture', seconds, 'a'*32),
                             wf.base.build('wan22_14b', 'fixture', seconds, 'a'*32))
        self.assertEqual(wf.workflow_hash_for_family('wan22_14b'), wf.base.workflow_hash())

    def test_reference_uses_real_i2v_experts_and_conditioning_not_text_experts(self):
        graph = wf.build('wan22_i2v_14b', 'fixture', 5, 'a'*32, 'owned-reference.png')
        self.assertEqual(graph['7']['class_type'], 'WanImageToVideo')
        self.assertEqual(graph['7']['inputs']['start_image'], ['input', 0])
        self.assertEqual(graph['input']['inputs']['image'], 'owned-reference.png')
        self.assertEqual(graph['8']['inputs']['latent_image'], ['7', 2])
        for key in ('8', '9'):
            self.assertEqual(graph[key]['inputs']['positive'], ['7', 0])
            self.assertEqual(graph[key]['inputs']['negative'], ['7', 1])
        for key in ('5', '6'):
            self.assertIn('i2v_', graph[key]['inputs']['unet_name'])
            self.assertNotIn('t2v_', graph[key]['inputs']['unet_name'])
        self.assertEqual(graph['9']['inputs']['latent_image'], ['8', 0])
        self.assertEqual(wf.profile('wan22_i2v_14b')['fps'], 16)

    def test_supplied_reference_never_silently_disappears(self):
        with self.assertRaisesRegex(wf.Failure, 'reference_image_required'):
            wf.build('wan22_i2v_14b', 'fixture', 5, 'a'*32)
        with self.assertRaisesRegex(wf.Failure, 'image_conditioning'):
            wf.build('wan22_14b', 'fixture', 5, 'a'*32, 'reference.png')

    def test_image_proof_binds_both_wrapper_and_inherited_text_graph_source(self):
        before = wf.workflow_hash_for_family('wan22_i2v_14b')
        with patch.object(wf.base, 'workflow_hash', return_value='b'*64):
            self.assertNotEqual(before, wf.workflow_hash_for_family('wan22_i2v_14b'))

    def test_unverified_or_stale_profiles_never_advertise_14b_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(wf.Failure, 'not_qualified'):
                wf.active_profile('video.animate_image', root, root)
            p = wf.profile('wan22_i2v_14b')
            (root/'quality-profile.json').write_text(json.dumps({'active': {'video.animate_image': {
                **p, 'status': 'verified', 'max_segment_seconds': 5, 'workflow_sha256': 'stale'}}}))
            with patch.object(wf, 'verify_models') as verify:
                with self.assertRaisesRegex(wf.Failure, 'not_qualified'):
                    wf.active_profile('video.animate_image', root, root)
                verify.assert_not_called()

    def test_profile_cannot_substitute_text_model_for_reference_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p = wf.profile('wan22_t2v_14b_lightx2v')
            entry = {**p, 'status': 'verified', 'max_segment_seconds': 5,
                     'workflow_sha256': wf.workflow_hash_for_family(p['family'])}
            (root/'quality-profile.json').write_text(json.dumps({'active': {'video.animate_image': entry}}))
            with patch.object(wf, 'verify_models') as verify:
                with self.assertRaisesRegex(wf.Failure, 'not_qualified'):
                    wf.active_profile('video.animate_image', root, root)
                verify.assert_not_called()

    def test_missing_lora_cannot_use_receipt_from_base_experts_only(self):
        family = 'wan22_t2v_14b_lightx2v'
        source = json.loads((ROOT/'backend/wan14/models.json').read_text())
        rows = [{**r, 'bytes': 1} for r in source if r['relative'] in wf.MODEL_FILES[family]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest = root/'models.json'
            manifest.write_text(json.dumps(rows))
            for row in rows:
                target = root/row['relative']; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'x')
            import hashlib
            receipt = {
                'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                'verified_files': [{k: r[k] for k in ('relative', 'bytes', 'sha256')}
                                   for r in rows if not r['relative'].startswith('loras/')]}
            (root/'models-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(wf.Failure, 'models_unavailable'):
                wf.verify_models(family, root, root)
            receipt['verified_files'] = [{k: r[k] for k in ('relative', 'bytes', 'sha256')} for r in rows]
            (root/'models-receipt.json').write_text(json.dumps(receipt))
            wf.verify_models(family, root, root)


if __name__ == '__main__':
    unittest.main()
