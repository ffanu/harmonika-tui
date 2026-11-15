import importlib.util
from pathlib import Path
import unittest
import tempfile
import hashlib
import os
from PIL import Image

SPEC=importlib.util.spec_from_file_location('rtx_profile',Path(__file__).resolve().parents[1]/'backend/rtx_queue/windows/image_profile.py')
profile=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(profile)


class ImageProfile(unittest.TestCase):
    def test_bridge_quality_preset_uses_local_distilled_model(self):
        self.assertEqual(profile.resolve_profile('fluxhd',28,lambda:'flux'),{'requested':'klein','preferred':'klein','klein_steps':4})

    def test_explicit_model_is_never_silently_changed(self):
        for engine in ('flux','qwen2512','klein'):
            with self.subTest(engine=engine):
                result=profile.resolve_profile(engine,12,lambda:self.fail('explicit selection must not call picker'))
                self.assertEqual(result['requested'],engine);self.assertEqual(result['preferred'],engine);self.assertEqual(result['klein_steps'],12)

    def test_existing_automatic_picker_is_preserved(self):
        self.assertEqual(profile.resolve_profile('auto',4,lambda:'qwen2512')['preferred'],'qwen2512')

    def test_invalid_profiles_and_steps_fail_before_execution(self):
        for requested,steps in [('cloud',4),(None,4),('fluxhd',True),('fluxhd',3),('fluxhd',41),('fluxhd','28')]:
            with self.subTest(requested=requested,steps=steps),self.assertRaises(ValueError):profile.resolve_profile(requested,steps,lambda:'flux')

    def test_quality_canvas_does_not_warp_or_override_explicit_model_dimensions(self):
        self.assertEqual(profile.quality_canvas('fluxhd',512,512),(1024,1024))
        self.assertEqual(profile.quality_canvas('fluxhd',2048,2048),(2048,2048))
        self.assertEqual(profile.quality_canvas('flux',512,512),(512,512))
        self.assertEqual(profile.quality_canvas('fluxhd',512,768),(512,768))

    def test_lossless_encoding_meets_existing_byte_gate_without_pixel_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'image.png';im=Image.new('RGB',(1024,1024),(24,88,160));before=hashlib.sha256(im.tobytes()).hexdigest();im.save(p)
            size=profile.ensure_png_minimum_bytes(p);self.assertGreaterEqual(size,2*1024*1024)
            with Image.open(p) as result:
                self.assertEqual(result.size,(1024,1024));self.assertEqual(result.mode,'RGB');self.assertEqual(hashlib.sha256(result.tobytes()).hexdigest(),before)
            self.assertEqual(list(Path(tmp).glob('.quality-*')),[])

    def test_impossible_small_image_is_rejected_without_replacing_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'tiny.png';Image.new('RGB',(32,32)).save(p);before=p.read_bytes()
            with self.assertRaisesRegex(ValueError,'image_quality_not_met'):profile.ensure_png_minimum_bytes(p)
            self.assertEqual(p.read_bytes(),before);self.assertEqual(list(Path(tmp).glob('.quality-*')),[])

    def test_existing_large_png_is_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'noise.png';im=Image.frombytes('RGB',(1024,1024),os.urandom(1024*1024*3));im.save(p);before=p.read_bytes()
            self.assertGreaterEqual(len(before),2*1024*1024);profile.ensure_png_minimum_bytes(p);self.assertEqual(p.read_bytes(),before)


if __name__=='__main__':unittest.main()
