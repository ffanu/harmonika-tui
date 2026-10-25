import importlib.util
import json
import hashlib
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('public_source',Path(__file__).resolve().parent.parent/'runtime/check-public-source.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Source(unittest.TestCase):
    def test_google_secrets_are_detected_without_returning_values(self):
        fixtures=[('ya29.'+'a'*40,'google_oauth_access_token'),('GOCSPX-'+'b'*24,'google_client_secret'),('AIza'+'c'*35,'google_api_key')]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for value,code in fixtures:
                (root/'google-config.json').write_text(json.dumps({'credential':value}))
                result=m.review([Path('google-config.json')],root)
                self.assertEqual(result[0]['code'],code)
                self.assertNotIn(value,json.dumps(result))

    def test_reviewed_media_requires_matching_checksum_and_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);assets=root/'docs/assets';assets.mkdir(parents=True)
            file=assets/'harmonika-demo.gif';data=b'GIF89a\0fixture';file.write_bytes(data)
            (assets/'manifest.json').write_text(json.dumps({file.name:{'sha256':hashlib.sha256(data).hexdigest()}}))
            relative=file.relative_to(root)
            self.assertEqual(m.review([relative],root),[])
            file.write_bytes(data+b'changed')
            self.assertEqual(m.review([relative],root)[0]['code'],'public_media_checksum_or_signature_failed')
    def test_detects_fake_secret_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);secret='hai_live_'+('a'*64);(root/'bad.py').write_text('key='+secret)
            report=m.review([Path('bad.py')],root)
            self.assertEqual(report[0]['code'],'api_token');self.assertNotIn(secret,json.dumps(report))
    def test_blocks_private_files_but_allows_generic_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'harmonika-api-key').write_text('sensitive');(root/'example.py').write_text('key="<YOUR_HARMONIKA_KEY>"')
            self.assertEqual(len(m.review([Path('harmonika-api-key'),Path('example.py')],root)),1)
    def test_does_not_follow_source_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'target').write_text('not-read');(root/'link').symlink_to(root/'target')
            self.assertEqual(m.review([Path('link')],root)[0]['code'],'symlink_requires_review')

if __name__=='__main__':unittest.main()
