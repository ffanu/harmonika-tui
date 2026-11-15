import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'backend/video_toolkit/stage_wangp_cache.py'
spec = importlib.util.spec_from_file_location('model_cache_test', SOURCE)
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class ModelCacheTest(unittest.TestCase):
    def test_pinned_runtime_includes_offline_preflight_dependencies(self):
        rows = cache.runtime_rows(json.loads((SOURCE.parent / 'models.json').read_text()))
        self.assertEqual(len(rows), 15)
        self.assertLess(sum(r['bytes'] for r in rows), 14 * 1024**3)
        self.assertTrue(any(r['file'] == 'Wan2.1_VAE.safetensors' for r in rows))
        self.assertTrue(any(r['file'].startswith('xlm-roberta-large/') for r in rows))
        self.assertFalse(any('14B' in r['file'] for r in rows))

    def test_corrupt_copy_never_publishes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); source = root / 'source'; source.write_bytes(b'corrupt')
            target = root / 'target'
            with self.assertRaisesRegex(ValueError, 'cache_hash_mismatch'):
                cache.verified_copy(source, target, 7, '0' * 64)
            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob('.model-*')))

    def test_existing_model_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); source = root / 'source'; source.write_bytes(b'new')
            target = root / 'target'; target.write_bytes(b'old')
            with self.assertRaisesRegex(ValueError, 'existing_cache_mismatch'):
                cache.verified_copy(source, target, 3, hashlib.sha256(b'new').hexdigest())
            self.assertEqual(target.read_bytes(), b'old')

    def test_valid_copy_is_atomic_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); source = root / 'source'; source.write_bytes(b'valid')
            target = root / 'target'
            cache.verified_copy(source, target, 5, hashlib.sha256(b'valid').hexdigest())
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertFalse(list(root.glob('.model-*')))

    def test_partial_cache_rejected_without_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                cache.ready_cache(Path(root))

    def test_forged_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); (root / 'receipt.json').write_text(json.dumps({'revision': cache.REVISION}))
            with self.assertRaisesRegex(ValueError, 'cache_receipt_mismatch'):
                cache.ready_cache(root)

    def test_receipt_does_not_hide_deleted_model(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); manifest = root / 'models.json'; manifest.write_text('[]')
            rows = [{'file': 'fixture', 'bytes': 5, 'sha256': hashlib.sha256(b'valid').hexdigest()}]
            receipt = {'revision': cache.REVISION,
                       'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                       'verified_files': rows}
            (root / 'receipt.json').write_text(json.dumps(receipt))
            with patch.object(cache, 'runtime_rows', return_value=rows):
                with self.assertRaisesRegex(ValueError, 'cache_inventory_mismatch'):
                    cache.ready_cache(root, root)
                (root / 'fixture').write_bytes(b'valid')
                self.assertEqual(cache.ready_cache(root, root), root)
                manifest.write_text('[{}]')
                with self.assertRaisesRegex(ValueError, 'cache_receipt_mismatch'):
                    cache.ready_cache(root, root)


if __name__ == '__main__':
    unittest.main()
