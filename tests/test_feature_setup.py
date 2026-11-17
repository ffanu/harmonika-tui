import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('feature_setup', SOURCE/'runtime/feature_setup.py')
m = importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def tar_fixture(file, entries):
    with tarfile.open(file, 'w:gz') as tar:
        for name, data, kind in entries:
            entry = tarfile.TarInfo(name)
            if kind == 'link':entry.type = tarfile.SYMTYPE;entry.linkname = '../../outside'
            else:entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    return hashlib.sha512(file.read_bytes()).digest()


class FeatureSetup(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','Legacy POSIX profile alias')
    def test_owned_private_legacy_profile_alias_is_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            area=Path(temp);target=area/'old-private-profile';target.mkdir(mode=0o700)
            alias=area/'harmonika-terminal';alias.symlink_to(target,target_is_directory=True)
            self.assertEqual(m.feature_root(alias),target.resolve())
            target.chmod(0o755)
            with self.assertRaisesRegex(m.SetupError,'unsafe_runtime_path'):m.feature_root(alias)
            self.assertEqual(target.stat().st_mode&0o777,0o755)

    @unittest.skipUnless(os.name=='posix','Legacy POSIX profile alias')
    def test_legacy_alias_does_not_permit_symlink_dependency_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            area=Path(temp);target=area/'private';target.mkdir(mode=0o700)
            alias=area/'profile';alias.symlink_to(target,target_is_directory=True)
            runtime=target/'python-runtime';runtime.symlink_to(area,target_is_directory=True)
            with self.assertRaisesRegex(m.SetupError,'unsafe_runtime_path'):m._private_directory(runtime)

    def test_intel_mac_uses_compatible_pinned_crypto_wheel(self):
        with patch.object(m.platform,'system',return_value='Darwin'),patch.object(m.platform,'machine',return_value='x86_64'):
            _,lock,versions=m._locks(SOURCE/'runtime')
            self.assertEqual(lock.name,'ssh-requirements-darwin-x64.lock')
            self.assertEqual(versions['cryptography'],'48.0.1')
        with patch.object(m.platform,'system',return_value='Windows'),patch.object(m.platform,'machine',return_value='AMD64'):
            _,_,versions=m._locks(SOURCE/'runtime')
            self.assertEqual(versions['cryptography'],'50.0.2')

    def test_signed_package_resources_are_preferred(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);(root/'tools').mkdir();lock=root/'tools/documents-requirements.lock';lock.write_text('synthetic==1.0\n')
            self.assertEqual(m._resource(root,'documents-requirements.lock'),lock)

    def test_core_only_does_not_provision_or_request_network(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'HARMONIKA_INSTALL_FEATURES':'0'}), patch.object(m,'setup_python') as python,patch.object(m,'setup_browser') as browser:
            report = m.ensure_features(Path(temp))
            self.assertEqual(report['status'],'core_only');self.assertFalse(report['ready'])
            python.assert_not_called();browser.assert_not_called()

    def test_component_failures_report_degraded_without_raw_error_or_fake_success(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'HARMONIKA_INSTALL_FEATURES':'1'}),patch.object(m,'setup_python',side_effect=m.SetupError('download_unavailable',True)),patch.object(m,'setup_browser',side_effect=RuntimeError('PRIVATE_LOG_SHOULD_NOT_APPEAR')):
            report=m.ensure_features(Path(temp))
            self.assertEqual(report['status'],'degraded');self.assertFalse(report['ready'])
            self.assertTrue(report['python']['retryable']);self.assertFalse(any(report['features'].values()))
            self.assertNotIn('PRIVATE_LOG',json.dumps(report));self.assertTrue((Path(temp)/'feature-setup-status.json').is_file())

    def test_missing_system_libraries_are_not_reported_as_browser_readiness(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'HARMONIKA_INSTALL_FEATURES':'1'}),patch.object(m,'setup_python',return_value={'status':'ready','ready':True,'python':'synthetic-python'}),patch.object(m,'setup_browser',side_effect=m.SetupError('browser_system_libraries_missing')):
            report=m.ensure_features(Path(temp));self.assertTrue(report['features']['documents']);self.assertFalse(report['features']['browser'])
            self.assertIn('system libraries',report['browser']['remedy']);self.assertFalse(report['ready'])

    def test_origin_policy_refuses_http_credentials_ports_and_foreign_redirects(self):
        for url in ['http://registry.npmjs.org/a','https://example.com/a','https://user:secret@registry.npmjs.org/a','https://registry.npmjs.org:8443/a']:
            with self.assertRaisesRegex(m.SetupError,'download_origin_denied'):m._url(url,{'registry.npmjs.org'})
        with self.assertRaisesRegex(m.SetupError,'download_origin_denied'):
            m.RestrictedRedirect({'registry.npmjs.org'}).redirect_request(None,None,302,'',{},'https://evil.invalid/a')

    def test_streaming_download_enforces_byte_limit_without_publishing_an_unverified_module(self):
        class Response(io.BytesIO):headers={}
        class Opener:
            def open(self,*a,**kw):return Response(b'x'*20)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(m.SetupError,'download_size_limit'):
                m.download('https://registry.npmjs.org/fixture',Path(temp)/'archive',{'registry.npmjs.org'},maximum=10,opener=Opener())

    def test_lock_rejects_unpinned_integrity_and_untrusted_package_path(self):
        good={'lockfileVersion':3,'packages':{'':{},'node_modules/playwright':{'version':'1.0','resolved':'https://registry.npmjs.org/playwright/-/playwright-1.0.tgz','integrity':'sha512-'+base64.b64encode(bytes(64)).decode()}}}
        self.assertEqual(len(m.npm_entries(good)),1)
        bad=json.loads(json.dumps(good));bad['packages']['node_modules/playwright']['integrity']='sha1-AAAA'
        with self.assertRaises(m.SetupError):m.npm_entries(bad)
        bad=json.loads(json.dumps(good));bad['packages']['../escape']=bad['packages'].pop('node_modules/playwright')
        with self.assertRaises(m.SetupError):m.npm_entries(bad)

    def test_valid_npm_tar_is_hashed_and_extracted_as_plain_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'fixture.tgz';digest=tar_fixture(archive,[('package/package.json',b'{"version":"1.0"}','file'),('package/cli.js',b'console.log(1)','file')])
            output=root/'module';output.mkdir();manifest=m.extract_npm(archive,output,digest)
            self.assertEqual((output/'cli.js').read_text(),'console.log(1)');self.assertEqual(len(manifest),2)
            self.assertTrue(m._manifest_healthy(output,manifest));(output/'cli.js').write_text('tampered');self.assertFalse(m._manifest_healthy(output,manifest))

    def test_hash_mismatch_never_extracts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'fixture.tgz';tar_fixture(archive,[('package/a.js',b'ok','file')]);output=root/'out';output.mkdir()
            with self.assertRaisesRegex(m.SetupError,'integrity_failed'):m.extract_npm(archive,output,bytes(64))
            self.assertEqual(list(output.iterdir()),[])

    def test_tar_traversal_and_links_cannot_write_outside_stage(self):
        for name,kind in [('package/../../outside','file'),('/absolute','file'),('package\\bad','file'),('package/link','link')]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=root/'fixture.tgz';digest=tar_fixture(archive,[(name,b'bad',kind)]);output=root/'out';output.mkdir()
                with self.assertRaises(m.SetupError):m.extract_npm(archive,output,digest)
                self.assertFalse((root/'outside').exists())

    def test_zip_node_extraction_only_copies_requested_regular_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'node.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('node-fixture/node.exe',b'node');z.writestr('node-fixture/ignored.txt',b'ignore')
            target=root/'node.exe';m.extract_node(archive,target,'node-fixture/node.exe')
            self.assertEqual(target.read_bytes(),b'node');self.assertFalse((root/'ignored.txt').exists())

    def test_zip_node_traversal_and_symlink_executable_are_refused(self):
        for kind in ['traversal','link']:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=root/'node.zip'
                with zipfile.ZipFile(archive,'w') as z:
                    if kind=='traversal':z.writestr('../outside',b'escape')
                    entry=zipfile.ZipInfo('node-fixture/node.exe')
                    if kind=='link':entry.create_system=3;entry.external_attr=0o120777<<16
                    z.writestr(entry,b'node')
                with self.assertRaises(m.SetupError):m.extract_node(archive,root/'node.exe','node-fixture/node.exe')
                self.assertFalse((root.parent/'outside').exists())

    def test_supported_node_platforms_are_explicit(self):
        for system,machine,want in [('Linux','x86_64','linux-x64'),('Linux','aarch64','linux-arm64'),('Darwin','arm64','darwin-arm64'),('Windows','AMD64','win-x64')]:
            with patch.object(m.platform,'system',return_value=system),patch.object(m.platform,'machine',return_value=machine):self.assertEqual(m._node_platform(),want)
        with patch.object(m.platform,'system',return_value='Linux'),patch.object(m.platform,'machine',return_value='riscv64'):
            with self.assertRaisesRegex(m.SetupError,'platform_unsupported'):m._node_platform()

    def test_subprocess_environment_excludes_provider_credentials(self):
        with patch.dict(os.environ,{'HARMONIKA_API_KEY':'fixture-private','OPENAI_API_KEY':'fixture-private','SYSTEMROOT':'C:\\Windows'}):
            env=m._environment();self.assertNotIn('HARMONIKA_API_KEY',env);self.assertNotIn('OPENAI_API_KEY',env)
            self.assertEqual(env['SYSTEMROOT'],'C:\\Windows')

    def test_requirement_locks_reject_script_options_and_version_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'documents-requirements.lock').write_text('--extra-index-url https://evil.invalid\n');(root/'ssh-requirements.lock').write_text('example>=1\n')
            with self.assertRaisesRegex(m.SetupError,'invalid_feature_lock'):m._locks(root)

    def test_managed_node_checksum_failure_does_not_publish_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def download_fixture(url,target,hosts,maximum=m.MAX_ARCHIVE):
                if str(target).endswith('SHASUMS256.txt'):
                    target.write_text('0'*64+'  node-v'+m.NODE_VERSION+'-linux-x64.tar.gz\n')
                else:target.write_bytes(b'untrusted fixture')
            with patch.dict(os.environ,{'HARMONIKA_NODE':'fixture-node18'}),patch.object(m,'_node_version',return_value='18.20.8'),patch.object(m,'_node_platform',return_value='linux-x64'),patch.object(m,'download',side_effect=download_fixture):
                with self.assertRaisesRegex(m.SetupError,'checksum_failed'):m.browser_node(root)
            self.assertFalse((root/'node-runtime/node').exists())

    def test_windows_reserved_and_stream_paths_are_refused_before_extraction(self):
        for name in ['package/NUL','package/COM1.txt','package/name:stream','C:/node.exe','package/bad.','package/bad ']:
            with self.subTest(name=name),self.assertRaises(m.SetupError):m._safe_name(name)


if __name__=='__main__':unittest.main()
