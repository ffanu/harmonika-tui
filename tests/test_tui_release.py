"""Tests for the Harmonika TUI plugin release tool (runtime/tools/tui_release.py).

Real Ed25519 crypto runs through the installed Node CLI with synthetic keys
created into a temporary directory. Coverage: build/verify/extract roundtrip,
manifest envelope shape, tampering, traversal/symlink/bomb/oversize refusal,
secrets filename refusal, source immutability, extraction cleanup and
idempotence, CLI behaviour, and the no-auto-key-creation guarantee.
"""
import base64
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'tui_release', ROOT / 'runtime/tools/tui_release.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

KEYGEN_JS = ("const fs=require('fs'),c=require('crypto');"
             "const k=c.generateKeyPairSync('ed25519');"
             "fs.writeFileSync(process.argv[1],k.privateKey.export({type:'pkcs8',format:'pem'}));"
             "fs.writeFileSync(process.argv[2],k.publicKey.export({type:'spki',format:'pem'}));")

SOURCE_FILES = {
    'plugin.json': b'{"name":"demo-plugin","api":"harmonika-tui-plugin-v2"}',
    'index.js': b'export function activate(host){ return true; }\n',
    'src/util.ts': b'export const answer = 42;\n',
    'docs/guide.md': b'# Demo plugin\n\nSynthetic fixture.\n',
    'tools/helper.py': b'DEF = "SYNTHETIC-PLUGIN-BODY"\n',
}

WORKDIR = None
NODE = None
PRIVATE_KEY = None
PUBLIC_KEY = None
OTHER_PUBLIC_KEY = None
SOURCE = None
BASE_PACKAGE = None


def setUpModule():
    global WORKDIR, NODE, PRIVATE_KEY, PUBLIC_KEY, OTHER_PUBLIC_KEY
    global SOURCE, BASE_PACKAGE
    NODE = module._node_command()
    WORKDIR = Path(tempfile.mkdtemp(prefix='tui-release-test-'))
    keys = WORKDIR / 'keys'
    keys.mkdir()
    PRIVATE_KEY = keys / 'signing-private.pem'
    PUBLIC_KEY = keys / 'trusted-public.pem'
    OTHER_PUBLIC_KEY = keys / 'other-public.pem'
    other_private = keys / 'other-private.pem'
    for private, public in ((PRIVATE_KEY, PUBLIC_KEY),
                            (other_private, OTHER_PUBLIC_KEY)):
        subprocess.run([NODE, '-e', KEYGEN_JS, str(private), str(public)],
                       check=True, capture_output=True)
    SOURCE = WORKDIR / 'source'
    for name, data in SOURCE_FILES.items():
        target = SOURCE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    BASE_PACKAGE = WORKDIR / 'base.zip'
    module.build_release(SOURCE, BASE_PACKAGE, '1.2.3', PRIVATE_KEY,
                         ['plugin.json'])


def tearDownModule():
    if WORKDIR is not None:
        shutil.rmtree(WORKDIR, ignore_errors=True)


def error_code(context):
    return context.exception.code


def read_entries(zip_path):
    with zipfile.ZipFile(str(zip_path)) as archive:
        return {info.filename: archive.read(info.filename)
                for info in archive.infolist()}


def write_entries(zip_path, entries):
    with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def base_manifest():
    envelope = json.loads(read_entries(BASE_PACKAGE)[module.MANIFEST_NAME])
    return json.loads(base64.b64decode(envelope['payload']))


def reseal(entries, manifest, signature=None):
    payload = json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True).encode()
    if signature is None:
        signature = base64.b64encode(b'A' * 64).decode('ascii')
    envelope = {'payload': base64.b64encode(payload).decode('ascii'),
                'signature': signature}
    crafted = dict(entries)
    crafted[module.MANIFEST_NAME] = json.dumps(
        envelope, sort_keys=True, separators=(',', ':')).encode()
    return crafted


class ContractTests(unittest.TestCase):
    def test_public_api_contract(self):
        self.assertEqual(module.PLUGIN_API, 'harmonika-tui-plugin-v2')
        self.assertEqual(module.MANIFEST_NAME, 'plugin-release.json')
        self.assertEqual(module.ALLOWED_SUFFIXES,
                         frozenset({'.js', '.mjs', '.cjs', '.json', '.md',
                                    '.py', '.ts', '.tsx'}))
        self.assertEqual(module.MAX_FILES, 200)
        self.assertEqual(module.MAX_TOTAL_BYTES, 50 * 1024 * 1024)
        self.assertEqual(sorted(module.__all__),
                         sorted(['PLUGIN_API', 'MANIFEST_NAME',
                                 'ALLOWED_SUFFIXES', 'MAX_FILES',
                                 'MAX_TOTAL_BYTES', 'ReleaseError',
                                 'build_release', 'verify_release',
                                 'extract_release', 'main']))
        for name in ('build_release', 'verify_release', 'extract_release'):
            self.assertTrue(callable(getattr(module, name)))


class BuildVerifyExtractTests(unittest.TestCase):
    def test_roundtrip_build_verify_extract(self):
        package = WORKDIR / 'roundtrip.zip'
        metadata = module.build_release(SOURCE, package, '2.0.0', PRIVATE_KEY)
        self.assertIs(metadata['built'], True)
        self.assertEqual(metadata['api'], 'harmonika-tui-plugin-v2')
        self.assertEqual(metadata['version'], '2.0.0')
        self.assertEqual(metadata['file_count'], len(SOURCE_FILES))
        self.assertIs(metadata['binary_included'], False)
        self.assertEqual(metadata['files'], sorted(SOURCE_FILES))
        verified = module.verify_release(package, PUBLIC_KEY)
        self.assertIs(verified['verified'], True)
        self.assertEqual(verified['package_sha256'], metadata['package_sha256'])
        destination = WORKDIR / 'extracted'
        extracted = module.extract_release(package, PUBLIC_KEY, destination)
        self.assertIs(extracted['extracted'], True)
        for name, data in SOURCE_FILES.items():
            self.assertEqual((destination / name).read_bytes(), data, name)

    def test_extracted_files_are_non_executable(self):
        destination = WORKDIR / 'extracted-modes'
        module.extract_release(BASE_PACKAGE, PUBLIC_KEY, destination)
        for path in destination.rglob('*'):
            mode = stat.S_IMODE(path.stat().st_mode)
            if path.is_dir():
                self.assertEqual(mode, 0o755)
            else:
                self.assertEqual(mode, 0o644)
                self.assertFalse(path.stat().st_mode & stat.S_IXUSR)

    def test_manifest_envelope_shape(self):
        envelope = json.loads(read_entries(BASE_PACKAGE)[module.MANIFEST_NAME])
        self.assertEqual(sorted(envelope), ['payload', 'signature'])
        manifest = json.loads(base64.b64decode(envelope['payload']))
        self.assertEqual(manifest['api'], 'harmonika-tui-plugin-v2')
        self.assertEqual(manifest['version'], '1.2.3')
        self.assertEqual(manifest['binary_included'], False)
        self.assertEqual(manifest['file_count'], len(manifest['files']))
        self.assertEqual(manifest['total_bytes'],
                         sum(spec['size'] for spec in manifest['files'].values()))
        for name, spec in manifest['files'].items():
            self.assertEqual(sorted(spec), ['sha256', 'size'])
            self.assertEqual(len(spec['sha256']), 64)
        base64.b64decode(envelope['signature'], validate=True)

    def test_build_is_deterministic_and_idempotent(self):
        first = WORKDIR / 'deterministic-1.zip'
        second = WORKDIR / 'deterministic-2.zip'
        meta_one = module.build_release(SOURCE, first, '1.2.3', PRIVATE_KEY)
        meta_two = module.build_release(SOURCE, second, '1.2.3', PRIVATE_KEY)
        self.assertEqual(meta_one['package_sha256'], meta_two['package_sha256'])
        self.assertEqual(first.read_bytes(), second.read_bytes())
        module.build_release(SOURCE, first, '1.2.3', PRIVATE_KEY)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_source_tree_is_immutable_during_build(self):
        def snapshot():
            state = {}
            for path in sorted(SOURCE.rglob('*')):
                info = path.lstat()
                state[path.relative_to(SOURCE).as_posix()] = (
                    info.st_mtime_ns, info.st_size,
                    'link' if path.is_symlink() else
                    'dir' if path.is_dir() else 'file')
            return state
        before = snapshot()
        module.build_release(SOURCE, WORKDIR / 'immutable.zip', '1.2.3',
                             PRIVATE_KEY)
        self.assertEqual(snapshot(), before)

    def test_metadata_is_sanitized(self):
        metadata = module.build_release(SOURCE, WORKDIR / 'sanitized.zip',
                                        '1.2.3', PRIVATE_KEY)
        dumped = json.dumps(metadata)
        self.assertNotIn('SYNTHETIC-PLUGIN-BODY', dumped)
        self.assertNotIn('PRIVATE KEY', dumped)
        self.assertNotIn('demo-plugin', dumped)
        for key in ('built', 'api', 'version', 'file_count', 'total_bytes',
                    'binary_included', 'manifest_name', 'package_size',
                    'package_sha256', 'files'):
            self.assertIn(key, metadata)

    def test_resigned_modified_package_verifies_and_is_detected_by_hash(self):
        entries = read_entries(BASE_PACKAGE)
        entries['index.js'] = entries['index.js'] + b'\nexport const extra = 1;\n'
        manifest = base_manifest()
        manifest['files']['index.js']['size'] = len(entries['index.js'])
        import hashlib
        manifest['files']['index.js']['sha256'] = hashlib.sha256(
            entries['index.js']).hexdigest()
        manifest['total_bytes'] = sum(
            spec['size'] for spec in manifest['files'].values())
        envelope = module._sign_manifest(manifest, PRIVATE_KEY)
        crafted = dict(entries)
        crafted[module.MANIFEST_NAME] = json.dumps(
            envelope, sort_keys=True, separators=(',', ':')).encode()
        package = WORKDIR / 'resigned.zip'
        write_entries(package, crafted)
        verified = module.verify_release(package, PUBLIC_KEY)
        self.assertEqual(verified['total_bytes'], manifest['total_bytes'])

    def test_missing_private_key_is_never_auto_created(self):
        key_dir = WORKDIR / 'keyless'
        key_dir.mkdir()
        before = sorted(os.listdir(key_dir))
        with self.assertRaises(module.ReleaseError) as ctx:
            module.build_release(SOURCE, WORKDIR / 'nokey.zip', '1.2.3',
                                 key_dir / 'absent.pem')
        self.assertEqual(error_code(ctx), 'private_key_missing')
        self.assertEqual(sorted(os.listdir(key_dir)), before)


class SourceHardeningTests(unittest.TestCase):
    def make_source(self, name):
        source = WORKDIR / name
        source.mkdir()
        (source / 'plugin.json').write_bytes(b'{}')
        return source

    def build(self, source, version='1.0.0', required=None):
        return module.build_release(source, WORKDIR / (source.name + '.zip'),
                                    version, PRIVATE_KEY, required)

    def test_disallowed_suffix_refused_including_native_binary(self):
        source = self.make_source('suffix-src')
        (source / 'native.bin').write_bytes(b'\x7fELF synthetic')
        (source / 'logo.png').write_bytes(b'PNG')
        (source / 'worker.wasm').write_bytes(b'\x00asm')
        with self.assertRaises(module.ReleaseError) as ctx:
            self.build(source)
        self.assertEqual(error_code(ctx), 'disallowed_suffix')

    def test_symlink_refused_for_files_and_directories(self):
        source = self.make_source('symlink-src')
        (source / 'real.js').write_bytes(b'export const real = true;\n')
        os.symlink('real.js', source / 'linked.js')
        with self.assertRaises(module.ReleaseError) as ctx:
            self.build(source)
        self.assertEqual(error_code(ctx), 'symlink_forbidden')
        source_two = self.make_source('symlink-dir-src')
        (source_two / 'realdir').mkdir()
        (source_two / 'realdir' / 'inner.js').write_bytes(b'x\n')
        os.symlink('realdir', source_two / 'linkeddir')
        with self.assertRaises(module.ReleaseError) as ctx:
            self.build(source_two)
        self.assertEqual(error_code(ctx), 'symlink_forbidden')

    def test_sensitive_and_private_paths_refused(self):
        cases = {
            'credentials.json': 'sensitive_filename',
            'token.py': 'sensitive_filename',
            'id_rsa.js': 'sensitive_filename',
            '.env': 'hidden_path_forbidden',
            '.config/settings.json': 'hidden_path_forbidden',
            'node_modules/pkg/index.js': 'forbidden_directory',
        }
        for name, code in cases.items():
            source = self.make_source('sensitive-src')
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'{}')
            with self.assertRaises(module.ReleaseError) as ctx:
                self.build(source)
            self.assertEqual(error_code(ctx), code, name)
            shutil.rmtree(source)

    def test_required_files_validation(self):
        source = self.make_source('required-src')
        (source / 'index.js').write_bytes(b'export const ok = true;\n')
        metadata = self.build(source, required=['plugin.json', 'index.js'])
        self.assertEqual(metadata['files'], ['index.js', 'plugin.json'])
        with self.assertRaises(module.ReleaseError) as ctx:
            self.build(source, required=['missing.json'])
        self.assertEqual(error_code(ctx), 'required_file_missing')
        for invalid in ('../escape.js', 'image.png', 'plugin-release.json'):
            with self.assertRaises(module.ReleaseError) as ctx:
                self.build(source, required=[invalid])
            self.assertEqual(error_code(ctx), 'invalid_required_file', invalid)

    def test_file_count_bound(self):
        source = self.make_source('many-src')
        for index in range(module.MAX_FILES + 1):
            (source / ('f%03d.js' % index)).write_bytes(b'x\n')
        with self.assertRaises(module.ReleaseError) as ctx:
            self.build(source)
        self.assertEqual(error_code(ctx), 'too_many_files')

    def test_size_bounds(self):
        source = self.make_source('oversize-src')
        (source / 'huge.js').write_bytes(b'0' * (module.MAX_TOTAL_BYTES + 1))
        with self.assertRaises(module.ReleaseError) as ctx:
            self.build(source)
        self.assertEqual(error_code(ctx), 'entry_too_large')
        source = self.make_source('toobig-src')
        for index in range(3):
            (source / ('part%d.js' % index)).write_bytes(
                b'0' * (18 * 1024 * 1024))
        with self.assertRaises(module.ReleaseError) as ctx:
            self.build(source)
        self.assertEqual(error_code(ctx), 'release_too_large')

    def test_version_bounds(self):
        source = self.make_source('version-src')
        (source / 'index.js').write_bytes(b'x\n')
        for version in ('1.0.0', '1.2', '1.0.0-rc.1', '2026.11.12'):
            self.build(source, version=version)
        for version in ('v1.0.0', '1.0.0.0.0.0', 'latest', '../evil',
                        'x' * 40, 1, None):
            with self.assertRaises(module.ReleaseError) as ctx:
                self.build(source, version=version)
            self.assertEqual(error_code(ctx), 'invalid_version', repr(version))

    def test_invalid_source_and_destination(self):
        with self.assertRaises(module.ReleaseError) as ctx:
            module.build_release(WORKDIR / 'absent-source',
                                 WORKDIR / 'x.zip', '1.0.0', PRIVATE_KEY)
        self.assertEqual(error_code(ctx), 'invalid_source')
        with self.assertRaises(module.ReleaseError) as ctx:
            module.build_release(SOURCE, WORKDIR / 'no-parent' / 'x.zip',
                                 '1.0.0', PRIVATE_KEY)
        self.assertEqual(error_code(ctx), 'invalid_destination')


class VerifyHardeningTests(unittest.TestCase):
    def craft(self, name, entries):
        package = WORKDIR / name
        write_entries(package, entries)
        return package

    def expect(self, package, code):
        with self.assertRaises(module.ReleaseError) as ctx:
            module.verify_release(package, PUBLIC_KEY)
        self.assertEqual(error_code(ctx), code)

    def test_not_a_zip(self):
        target = WORKDIR / 'fake.zip'
        target.write_bytes(b'not a zip archive')
        self.expect(target, 'invalid_archive')

    def test_content_tampering_detected(self):
        entries = read_entries(BASE_PACKAGE)
        same_length = bytearray(entries['index.js'])
        same_length[0] = ord('X') if same_length[0] != ord('X') else ord('Y')
        entries['index.js'] = bytes(same_length)
        self.expect(self.craft('tamper-hash.zip', entries), 'hash_mismatch')
        entries = read_entries(BASE_PACKAGE)
        entries['index.js'] = entries['index.js'] + b'trailing'
        self.expect(self.craft('tamper-size.zip', entries), 'size_mismatch')

    def test_manifest_modification_breaks_signature(self):
        entries = read_entries(BASE_PACKAGE)
        manifest = base_manifest()
        manifest['version'] = '9.9.9'
        crafted = reseal(entries, manifest,
                         json.loads(entries[module.MANIFEST_NAME])['signature'])
        self.expect(self.craft('tamper-manifest.zip', crafted),
                    'signature_invalid')

    def test_wrong_public_key_rejected(self):
        with self.assertRaises(module.ReleaseError) as ctx:
            module.verify_release(BASE_PACKAGE, OTHER_PUBLIC_KEY)
        self.assertEqual(error_code(ctx), 'signature_invalid')

    def test_non_canonical_payload_rejected(self):
        entries = read_entries(BASE_PACKAGE)
        manifest = base_manifest()
        payload = json.dumps(manifest, sort_keys=True).encode()
        envelope = {'payload': base64.b64encode(payload).decode('ascii'),
                    'signature': base64.b64encode(b'B' * 64).decode('ascii')}
        crafted = dict(entries)
        crafted[module.MANIFEST_NAME] = json.dumps(envelope).encode()
        self.expect(self.craft('noncanon.zip', crafted), 'canonical_mismatch')

    def test_manifest_schema_rejections(self):
        entries = read_entries(BASE_PACKAGE)
        cases = []
        manifest = base_manifest()
        manifest['api'] = 'harmonika-tui-plugin-v1'
        cases.append(('api-mismatch.zip', reseal(entries, manifest), 'api_mismatch'))
        manifest = base_manifest()
        manifest['binary_included'] = True
        cases.append(('binary.zip', reseal(entries, manifest), 'binary_bundled'))
        manifest = base_manifest()
        manifest['file_count'] = manifest['file_count'] + 1
        cases.append(('count.zip', reseal(entries, manifest),
                      'manifest_count_mismatch'))
        manifest = base_manifest()
        manifest['total_bytes'] = manifest['total_bytes'] + 1
        cases.append(('totals.zip', reseal(entries, manifest),
                      'manifest_size_mismatch'))
        manifest = base_manifest()
        manifest['files']['rogue.js'] = {'sha256': '0' * 64, 'size': 1}
        manifest['file_count'] += 1
        manifest['total_bytes'] += 1
        cases.append(('rogue-listed.zip', reseal(entries, manifest),
                      'missing_manifest_file'))
        manifest = base_manifest()
        manifest['files'].pop('index.js')
        manifest['file_count'] -= 1
        manifest['total_bytes'] -= len(entries['index.js'])
        cases.append(('dropped-listed.zip', reseal(entries, manifest),
                      'unmanifested_file'))
        for name, crafted, code in cases:
            self.expect(self.craft(name, crafted), code)

    def test_structural_entry_rejections(self):
        cases = [
            ('../evil.js', b'x', 'path_traversal'),
            ('/tmp/evil.js', b'x', 'absolute_entry'),
            ('C:/evil.js', b'x', 'absolute_entry'),
            ('a\\b.js', b'x', 'unsafe_entry_name'),
            ('a//b.js', b'x', 'unsafe_entry_name'),
            ('.hidden.js', b'x', 'hidden_path_forbidden'),
            ('node_modules/pkg.js', b'x', 'forbidden_directory'),
            ('credentials.json', b'x', 'sensitive_filename'),
            ('evil.png', b'x', 'disallowed_suffix'),
        ]
        for name, data, code in cases:
            self.expect(self.craft('entry-%s.zip' % code,
                                   {name: data,
                                    module.MANIFEST_NAME: b'{}'}), code)

    def test_duplicate_entries_rejected(self):
        package = WORKDIR / 'duplicate.zip'
        with zipfile.ZipFile(str(package), 'w') as archive:
            archive.writestr('index.js', b'a\n')
            archive.writestr('index.js', b'b\n')
            archive.writestr(module.MANIFEST_NAME, b'{}')
        self.expect(package, 'duplicate_entry')

    def test_manifest_missing(self):
        entries = {name: data for name, data in read_entries(BASE_PACKAGE).items()
                   if name != module.MANIFEST_NAME}
        self.expect(self.craft('nomanifest.zip', entries), 'manifest_missing')

    def test_archive_bomb_rejected(self):
        self.expect(self.craft('bomb.zip',
                               {'bomb.js': b'\x00' * 5000000,
                                module.MANIFEST_NAME: b'{}'}),
                    'archive_bomb')

    def test_oversize_entry_rejected(self):
        self.expect(self.craft('oversize.zip',
                               {'big.js': b'\x00' * (module.MAX_TOTAL_BYTES + 1),
                                module.MANIFEST_NAME: b'{}'}),
                    'entry_too_large')

    def test_too_many_entries_rejected(self):
        entries = {module.MANIFEST_NAME: b'{}'}
        for index in range(module.MAX_FILES + 1):
            entries['f%03d.js' % index] = b'x'
        self.expect(self.craft('crowded.zip', entries), 'too_many_files')

    def test_envelope_shape_rejections(self):
        entries = read_entries(BASE_PACKAGE)
        crafted = dict(entries)
        crafted[module.MANIFEST_NAME] = b'not json'
        self.expect(self.craft('raw.zip', crafted), 'manifest_invalid')
        crafted[module.MANIFEST_NAME] = json.dumps({'payload': '!!!'}).encode()
        self.expect(self.craft('badb64.zip', crafted), 'manifest_invalid')
        crafted[module.MANIFEST_NAME] = json.dumps(
            {'payload': base64.b64encode(b'not json').decode(),
             'signature': base64.b64encode(b'C' * 64).decode()}).encode()
        self.expect(self.craft('badpayload.zip', crafted), 'manifest_invalid')

    def test_node_failure_maps_to_stable_code(self):
        original = module._node_command
        def broken():
            raise module.ReleaseError('node_missing')
        module._node_command = broken
        try:
            with self.assertRaises(module.ReleaseError) as ctx:
                module.verify_release(BASE_PACKAGE, PUBLIC_KEY)
            self.assertEqual(error_code(ctx), 'node_missing')
        finally:
            module._node_command = original


class ExtractTests(unittest.TestCase):
    def test_extract_replaces_existing_empty_directory(self):
        destination = WORKDIR / 'empty-dest'
        destination.mkdir()
        module.extract_release(BASE_PACKAGE, PUBLIC_KEY, destination)
        self.assertEqual(
            (destination / 'plugin.json').read_bytes(),
            SOURCE_FILES['plugin.json'])

    def test_extract_refuses_non_empty_destination_and_keeps_content(self):
        destination = WORKDIR / 'busy-dest'
        destination.mkdir()
        (destination / 'existing.txt').write_bytes(b'keep me')
        with self.assertRaises(module.ReleaseError) as ctx:
            module.extract_release(BASE_PACKAGE, PUBLIC_KEY, destination)
        self.assertEqual(error_code(ctx), 'destination_not_empty')
        self.assertEqual((destination / 'existing.txt').read_bytes(), b'keep me')

    def test_failed_extraction_cleans_up_completely(self):
        entries = read_entries(BASE_PACKAGE)
        entries['index.js'] = b'X' + entries['index.js'][1:]
        tampered = WORKDIR / 'extract-tampered.zip'
        write_entries(tampered, entries)
        parent = WORKDIR / 'extract-fail-parent'
        parent.mkdir()
        destination = parent / 'out'
        destination.mkdir()
        for package, code in ((tampered, 'hash_mismatch'),
                              (WORKDIR / 'fake.zip', 'invalid_archive')):
            if code == 'invalid_archive':
                (WORKDIR / 'fake.zip').write_bytes(b'nope')
            with self.assertRaises(module.ReleaseError) as ctx:
                module.extract_release(package, PUBLIC_KEY, destination)
            self.assertEqual(error_code(ctx), code)
            self.assertEqual(os.listdir(parent), ['out'])
            self.assertEqual(os.listdir(destination), [])

    def test_extraction_is_repeatable(self):
        for suffix in ('a', 'b'):
            destination = WORKDIR / ('repeat-' + suffix)
            module.extract_release(BASE_PACKAGE, PUBLIC_KEY, destination)
            self.assertEqual(
                (destination / 'index.js').read_bytes(),
                SOURCE_FILES['index.js'])
        self.assertTrue((WORKDIR / 'repeat-a' / 'index.js').exists())


class CliTests(unittest.TestCase):
    def run_cli(self, *args, stdin=None):
        return subprocess.run(
            [sys.executable, str(ROOT / 'runtime/tools/tui_release.py'), *args],
            input=stdin, capture_output=True)

    def test_cli_build_verify_extract_roundtrip(self):
        package = WORKDIR / 'cli.zip'
        built = self.run_cli('build', '--source', str(SOURCE), '--output',
                             str(package), '--version', '3.1.4',
                             '--private-key', str(PRIVATE_KEY),
                             '--require', 'plugin.json')
        self.assertEqual(built.returncode, 0, built.stderr)
        payload = json.loads(built.stdout)
        self.assertIs(payload['ok'], True)
        self.assertEqual(payload['result']['version'], '3.1.4')
        verified = self.run_cli('verify', '--package', str(package),
                                '--public-key', str(PUBLIC_KEY))
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIs(json.loads(verified.stdout)['ok'], True)
        destination = WORKDIR / 'cli-extract'
        extracted = self.run_cli('extract', '--package', str(package),
                                 '--public-key', str(PUBLIC_KEY),
                                 '--destination', str(destination))
        self.assertEqual(extracted.returncode, 0, extracted.stderr)
        self.assertIs(json.loads(extracted.stdout)['ok'], True)
        self.assertTrue((destination / 'plugin.json').exists())

    def test_cli_private_key_via_stdin_is_never_echoed(self):
        package = WORKDIR / 'cli-stdin-key.zip'
        result = self.run_cli('build', '--source', str(SOURCE), '--output',
                              str(package), '--version', '1.0.0',
                              '--private-key', '-',
                              stdin=PRIVATE_KEY.read_bytes())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(json.loads(result.stdout)['ok'], True)
        combined = result.stdout + result.stderr
        self.assertNotIn(b'PRIVATE KEY', combined)
        self.assertNotIn(PRIVATE_KEY.read_bytes(), combined)

    def test_cli_errors_are_machine_coded(self):
        result = self.run_cli('build', '--source', str(SOURCE), '--output',
                              str(WORKDIR / 'cli-bad.zip'), '--version', 'bad',
                              '--private-key', str(PRIVATE_KEY))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout),
                         {'ok': False, 'error': 'invalid_version'})
        result = self.run_cli('verify', '--package', str(BASE_PACKAGE),
                              '--public-key', str(OTHER_PUBLIC_KEY))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout),
                         {'ok': False, 'error': 'signature_invalid'})

    def test_cli_does_not_create_signing_keys(self):
        key_dir = WORKDIR / 'cli-keyless'
        key_dir.mkdir()
        result = self.run_cli('build', '--source', str(SOURCE), '--output',
                              str(WORKDIR / 'cli-nokey.zip'), '--version',
                              '1.0.0', '--private-key', str(key_dir / 'no.pem'))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['error'],
                         'private_key_missing')
        self.assertEqual(os.listdir(key_dir), [])


class ActivationRaceTests(unittest.TestCase):
    def test_archive_replacement_after_verification_cannot_change_extracted_code(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            package=Path(tmp)/'release.zip';shutil.copyfile(BASE_PACKAGE,package)
            destination=Path(tmp)/'installed'
            verify=module.verify_release
            def replace_after_verify(*args):
                result=verify(*args)
                entries=read_entries(package);entries['index.js']=b'UNVERIFIED_REPLACEMENT_CODE';write_entries(package,entries)
                return result
            with patch.object(module,'verify_release',side_effect=replace_after_verify):
                with self.assertRaises(module.ReleaseError):module.extract_release(package,PUBLIC_KEY,destination)
            self.assertFalse(destination.exists())

    def test_named_pipe_source_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();os.mkfifo(root/'helper.py')
            with self.assertRaises(module.ReleaseError):module.build_release(root,Path(tmp)/'out.zip','1.2.3',PRIVATE_KEY)


if __name__ == '__main__':
    unittest.main()
