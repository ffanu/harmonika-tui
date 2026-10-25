"""Signed platform pins and staged upgrades; fixture binaries never touch user TUI."""
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import test_releases as base
m=base.m

class PlatformReleaseIntegrity(unittest.TestCase):
 def setUp(self):
  self.f=base.Releases('test_signed_install_and_real_rollback');self.f.setUp();self.addCleanup(self.f.doCleanups)
 def signed(self,release,blob=None):
  manifest=self.f.manifest(release);manifest['binary_hashes']={p:base.sha(blob or self.f.binary) for p in ('darwin-arm64','darwin-x64','linux-x64','linux-arm64','windows-x64')};return manifest
 def test_platform_normalization(self):
  for system,arch,want in [('Windows','AMD64','windows-x64'),('Linux','aarch64','linux-arm64'),('Darwin','arm64','darwin-arm64')]:
   with patch.object(m.platform,'system',return_value=system),patch.object(m.platform,'machine',return_value=arch):self.assertEqual(m.platform_id(),want)
 def test_signed_platform_pin_overrides_primary_architecture(self):
  current,_=m.active(self.f.root);v=self.signed(current.name);v['binary_sha256']='f'*64;(current/'release.json').write_bytes(self.f.envelope(v));self.assertEqual(m.active(self.f.root)[0],current)
 def test_missing_or_malformed_platform_is_rejected(self):
  for v,code in [({'binary_hashes':{}},'platform_binary_unavailable'),({'binary_hashes':[]},'invalid_platform_checksums'),({'binary_hashes':{'windows-x64':'bad'}},'invalid_binary_checksum')]:
   with self.assertRaisesRegex(ValueError,code):m.expected_binary_sha(v,'windows-x64')
 def test_windows_unsigned_sidecar_cannot_override_signed_hash(self):
  current,_=m.active(self.f.root);v=self.signed(current.name);(current/'release.json').write_bytes(self.f.envelope(v));bad=b'changed';(current/'harmonika').rename(current/'harmonika.exe');(current/'harmonika.exe').write_bytes(bad);(current/'binary.sha256').write_text(base.sha(bad))
  with patch.object(m,'os',types.SimpleNamespace(name='nt')),patch.object(m,'platform_id',return_value='windows-x64'):
   with self.assertRaisesRegex(ValueError,'binary_checksum_mismatch'):m.active(self.f.root)
 def test_verified_binary_upgrade_preserves_original_and_rollback(self):
  original,_=m.active(self.f.root);blob=self.f.binary+b'# new signed fixture\n';source=self.f.root/'candidate';source.write_bytes(blob);v=self.signed('2026.10.24.2',blob);v['binary_sha256']=base.sha(blob)
  result=m.install_package(self.f.package(v),self.f.root,binary_source=source)
  self.assertEqual(result['release'],'2026.10.24.2');self.assertEqual((original/'harmonika').read_bytes(),self.f.binary);self.assertEqual(m.active(self.f.root)[0].joinpath('harmonika').read_bytes(),blob);self.assertEqual(m.rollback(self.f.root)['release'],original.name)
 def test_unverified_binary_upgrade_leaves_active_untouched(self):
  source=self.f.root/'candidate';source.write_bytes(b'bad')
  with self.assertRaisesRegex(ValueError,'platform_binary_checksum_mismatch'):m.install_package(self.f.package(self.signed('2026.10.24.2')),self.f.root,binary_source=source)
  self.f.unchanged()
 def test_upgraded_package_still_rejects_traversal(self):
  source=self.f.root/'candidate';source.write_bytes(self.f.binary)
  with self.assertRaisesRegex(ValueError,'unsafe_package_path'):m.install_package(self.f.package(self.signed('2026.10.24.2'),{**self.f.files,'../outside':b'bad'}),self.f.root,binary_source=source)
  self.f.unchanged();self.assertFalse((self.f.root/'outside').exists())
 def test_failed_preflight_does_not_modify_old_binary(self):
  original,_=m.active(self.f.root);source=self.f.root/'candidate';source.write_bytes(self.f.binary);self.f.files['tools/helper.cjs']=b'const =;'
  with self.assertRaisesRegex(ValueError,'javascript_preflight'):m.install_package(self.f.package(self.signed('2026.10.24.2')),self.f.root,binary_source=source)
  self.f.unchanged();self.assertEqual((original/'harmonika').read_bytes(),self.f.binary)
 def installer(self):
  spec=importlib.util.spec_from_file_location('fixture_bootstrap',base.SOURCE/'runtime/install.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
 def test_fresh_bootstrap_checks_signed_platform_not_primary_pin(self):
  i=self.installer();source=self.f.root/'candidate';source.write_bytes(self.f.binary);v=self.signed('2026.10.24.2');v['binary_sha256']='f'*64;stage=self.f.root/'stage';stage.mkdir()
  with patch.object(i,'ROOT',self.f.root),patch.object(i,'BINARY',source),patch.object(i,'detect_platform',return_value=m.platform_id()):i._prepare(self.f.package(v),stage)
  self.assertEqual((stage/'harmonika').read_bytes(),self.f.binary)
 def test_legacy_skip_flag_cannot_bypass_signed_platform_pin(self):
  i=self.installer();source=self.f.root/'candidate';source.write_bytes(b'bad');stage=self.f.root/'stage';stage.mkdir()
  with patch.object(i,'ROOT',self.f.root),patch.object(i,'BINARY',source),patch.object(i,'detect_platform',return_value=m.platform_id()),patch.dict(i.os.environ,{'HARMONIKA_SKIP_BINARY_SHA_CHECK':'1'}):
   with self.assertRaisesRegex(ValueError,'custom_binary_changed'):i._prepare(self.f.package(self.signed('2026.10.24.2')),stage)

if __name__=='__main__':unittest.main()
