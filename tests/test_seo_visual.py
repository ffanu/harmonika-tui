import ctypes
import errno
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/tools'))
import seo_visual as visual

class VisualTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='harmonika-seo-visual-test-');self.root=Path(self.tmp.name).resolve();self.addCleanup(self.tmp.cleanup)
    def test_atomic_directory_publication_is_exclusive(self):
        source=self.root/'source';source.mkdir();(source/'file').write_text('new')
        dest=self.root/'destination'
        try:visual.publish_directory(source,dest)
        except OSError as e:
            if e.errno in (errno.ENOTSUP,errno.ENOSYS):self.skipTest('OS exclusive rename unsupported')
            raise
        self.assertFalse(source.exists());self.assertEqual((dest/'file').read_text(),'new')
        source.mkdir();(source/'other').write_text('other')
        with self.assertRaises(OSError):visual.publish_directory(source,dest)
        self.assertTrue(source.exists());self.assertEqual((dest/'file').read_text(),'new')
    def test_failed_capture_does_not_occupy_final_destination(self):
        dest=self.root/'out'
        with patch.object(visual,'_capture_staged',return_value={'status':'degraded','reason':'fixture_launch_failed','checks':[]}):
            result=visual.capture({'url':'https://example.com/','output_dir':str(dest)},None)
        self.assertFalse(dest.exists());self.assertEqual(result['artifacts'],[])
        self.assertEqual(list(self.root.iterdir()),[])
    def test_success_publishes_only_after_both_captures(self):
        dest=self.root/'out'
        def stub(payload,fetcher):
            self.assertFalse(dest.exists());stage=Path(payload['output_dir']);stage.mkdir()
            checks=[]
            for name in ('desktop','mobile'):
                file=stage/(name+'.png');file.write_bytes(b'fixture-only')
                checks.append({'name':name,'screenshot':str(file)})
            return {'status':'degraded','checks':checks,'limitations':['fixture; not live screenshots']}
        with patch.object(visual,'_capture_staged',side_effect=stub):result=visual.capture({'url':'https://example.com/','output_dir':str(dest)},None)
        self.assertTrue((dest/'report.json').is_file())
        self.assertEqual(result['checks'][0]['screenshot'],str(dest/'desktop.png'))
        self.assertEqual(json.loads((dest/'report.json').read_text())['network_budget']['requests_per_viewport'],20)
        with self.assertRaises(FileExistsError):visual.capture({'url':'https://example.com/','output_dir':str(dest)},None)
    def test_missing_engine_stays_unavailable(self):
        with patch.object(visual,'_capture_staged',return_value={'status':'unavailable','reason':'browser_runtime_missing'}):
            result=visual.capture({'url':'https://example.com/','output_dir':str(self.root/'out')},None)
        self.assertEqual(result['status'],'unavailable');self.assertFalse((self.root/'out').exists())

if __name__=='__main__':unittest.main(verbosity=2)
