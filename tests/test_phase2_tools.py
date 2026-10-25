"""Phase2 TOOLS worker tests: phase2_tools.py + phase2_exec.py.

Run under the pinned tools venv (black/ruff/mypy/coverage/rope on PATH is the
same interpreter running these tests) with HARMONIKA_VENDOR_NODE pointing at a
node_modules containing prettier/tsc/esbuild, and HARMONIKA_PKG_CACHE holding
the offline allowlist artifacts. Anything genuinely unavailable is skipped
with the reason stated, never faked.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO, 'backend', 'coding_runtime')
sys.path.insert(0, TOOLS_DIR)
sys.path.insert(0, REPO)

import phase2_exec  # noqa: E402
import phase2_tools  # noqa: E402
from phase2_tools import ToolError, run, validate_rel_path  # noqa: E402

PYZ = phase2_tools  # short alias

PKG_CACHE = phase2_tools.PKG_CACHE
VENDOR_NODE = phase2_tools.VENDOR_NODE
DEPS_PY = phase2_tools.DEPS_PY
DEPS_NODE = phase2_tools.DEPS_NODE

GOOD_PY = 'def total(amount):\n    return amount + 1\n'
UGLY_PY = 'def  total( amount ) :\n    return  amount+1\n'


def _have_module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _child_ok(argv):
    try:
        return subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _py_toolchain():
    return _child_ok([sys.executable, '-m', 'black', '--version']) and \
        _child_ok([sys.executable, '-m', 'mypy', '--version']) and \
        _child_ok([sys.executable, '-m', 'coverage', '--version']) and \
        _have_module('rope') and bool(shutil.which('ruff'))


def _node_toolchain():
    return bool(shutil.which('node')) and bool(shutil.which('npm')) and \
        os.path.isfile(os.path.join(VENDOR_NODE, 'node_modules', '.bin', 'prettier')) and \
        os.path.isfile(os.path.join(VENDOR_NODE, 'node_modules', '.bin', 'tsc')) and \
        os.path.isfile(os.path.join(VENDOR_NODE, 'node_modules', '.bin', 'esbuild'))


def _pkg_cache_ready():
    if not shutil.which('npm'):
        return False
    py = os.path.isdir(os.path.join(PKG_CACHE, 'python')) and any(
        f.startswith('humanize-4.12.2-') and f.endswith('.whl')
        for f in os.listdir(os.path.join(PKG_CACHE, 'python')))
    node = os.path.isfile(os.path.join(PKG_CACHE, 'node', 'decimal.js-10.5.0.tgz'))
    return py and node


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode('utf-8')).decode('ascii')


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel, content):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(content)
        return rel

    def read(self, rel):
        with open(os.path.join(self.root, rel)) as fh:
            return fh.read()


# ---------------------------------------------------------------------------
# Pure units: path security and envelope
# ---------------------------------------------------------------------------
class PathSecurity(WorkspaceCase):
    def test_traversal_absolute_backslash_control(self):
        for bad in ('../x.py', 'a/../../x.py', '/etc/passwd', 'C:/x.py', '~/x.py',
                    'a\\b.py', 'a\x00b.py', 'a\nb.py', '', '.'):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolError):
                    validate_rel_path(bad)

    def test_credential_names_rejected(self):
        for bad in ('.env', 'creds/.env.local', 'x/secrets.py', 'a/id_rsa',
                    'server.pem', 'my.key', 'passwords.txt', 'credentials.json'):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolError) as caught:
                    validate_rel_path(bad)
                self.assertEqual(caught.exception.code, 'credential_path')

    def test_clean_path_normalises(self):
        self.assertEqual(validate_rel_path('pkg/x.py'), 'pkg/x.py')
        self.assertEqual(validate_rel_path('a/./b.py'), 'a/b.py')
        self.assertEqual(validate_rel_path('a//b.py'), 'a/b.py')

    def test_symlink_file_rejected(self):
        self.write('real.py', GOOD_PY)
        os.symlink(os.path.join(self.root, 'real.py'), os.path.join(self.root, 'link.py'))
        envelope = run('lint.run', {'path': 'link.py'}, root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'symlink_rejected')

    def test_symlink_dir_component_rejected(self):
        self.write('outside/x.py', GOOD_PY)
        os.symlink(os.path.join(self.root, 'outside'), os.path.join(self.root, 'sneaky'))
        envelope = run('lint.run', {'path': 'sneaky/x.py'}, root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertIn(envelope['error']['code'], ('symlink_rejected', 'not_found'))

    def test_hardlink_rejected(self):
        self.write('one.py', GOOD_PY)
        os.link(os.path.join(self.root, 'one.py'), os.path.join(self.root, 'two.py'))
        envelope = run('lint.run', {'path': 'two.py'}, root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'hardlinked_file')

    def test_oversized_file_rejected(self):
        self.write('big.py', 'x = "' + 'a' * (513 * 1024) + '"\n')
        envelope = run('lint.run', {'path': 'big.py'}, root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'file_too_large')

    def test_directory_and_missing_targets(self):
        os.makedirs(os.path.join(self.root, 'adir'))
        for path, code in (('adir', 'is_a_directory'), ('nope.py', 'not_found')):
            with self.subTest(path=path):
                envelope = run('lint.run', {'path': path}, root=self.root)
                self.assertEqual(envelope['error']['code'], code)


class Envelope(WorkspaceCase):
    def test_unknown_skill_and_args(self):
        cases = [
            ('nope.skill', {}),
            ('lint.run', {'path': 'x.py', 'extra': 1}),
            ('lint.run', {}),
            ('refactor.rename', {'path': 'x.py', 'line': 1}),  # missing column/new_name
        ]
        for skill, args in cases:
            with self.subTest(skill=skill):
                envelope = run(skill, args, root=self.root)
                self.assertFalse(envelope['ok'])
                self.assertIn(envelope['error']['code'],
                              ('unknown_skill', 'unknown_argument', 'missing_argument'))

    def test_expected_hash_format(self):
        self.write('a.py', GOOD_PY)
        envelope = run('code.format', {'path': 'a.py', 'expected_sha256': 'zz'}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'invalid_hash')

    def test_invalid_root(self):
        envelope = run('lint.run', {'path': 'a.py'}, root=os.path.join(self.root, 'gone'))
        self.assertEqual(envelope['error']['code'], 'invalid_root')

    def test_run_never_raises(self):
        for skill, args in (('lint.run', None), ('code.format', 'notadict'),
                            ('refactor.extract', {'path': 'a.py', 'start_line': True,
                                                  'end_line': 2, 'name': 'x'})):
            with self.subTest(skill=skill):
                self.assertIsInstance(run(skill, args, root=self.root), dict)

    def test_list_subcommand(self):
        proc = subprocess.run([sys.executable, os.path.join(TOOLS_DIR, 'phase2_tools.py'), 'list'],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(len(payload['result']['skills']), 13)

    def test_cli_invalid_encoding_and_usage(self):
        script = os.path.join(TOOLS_DIR, 'phase2_tools.py')
        proc = subprocess.run([sys.executable, script, 'run', 'lint.run', '!!!notb64!!!'],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)['error']['code'], 'invalid_encoding')
        proc = subprocess.run([sys.executable, script, 'bogus'], capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)

    def test_cli_bad_skill_fails_nonzero(self):
        script = os.path.join(TOOLS_DIR, 'phase2_tools.py')
        proc = subprocess.run([sys.executable, script, 'run', 'lint.run', b64({})],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)['error']['code'], 'missing_argument')


class PlanAdapter(unittest.TestCase):
    def test_fixed_argv_for_every_skill(self):
        sample = {
            'code.format': {'path': 'a.py'},
            'format.run': {'path': 'a.py'},
            'lint.run': {'path': 'a.py'},
            'type.check': {'path': 'a.py'},
            'build.run': {'path': 'a.py'},
            'pkg.list': {'manager': 'python'},
            'pkg.install': {'manager': 'python', 'name': 'humanize', 'version': '4.12.2'},
            'test.single': {'runner': 'python_unittest', 'path': 't.py', 'name': 't.T.test_a'},
            'test.coverage': {'runner': 'python_unittest'},
            'code.rename': {'path': 'a.py', 'line': 1, 'column': 0, 'new_name': 'b'},
            'refactor.rename': {'path': 'a.py', 'line': 1, 'column': 0, 'new_name': 'b'},
            'refactor.extract': {'path': 'a.py', 'start_line': 1, 'end_line': 2, 'name': 'm'},
            'refactor.dead_code': {'path': 'a.py'},
        }
        self.assertEqual(set(sample), set(phase2_exec.SKILL_IDS))
        for skill, args in sample.items():
            with self.subTest(skill=skill):
                plan = phase2_exec.build_phase2_plan(skill, args)
                self.assertEqual(set(plan), {'steps', 'step_timeout', 'overall_default'})
                self.assertEqual(len(plan['steps']), 1)
                argv = plan['steps'][0]
                self.assertEqual(argv[:3], ['python3', '/opt/harmonika/phase2_tools.py', 'run'])
                self.assertEqual(argv[3], skill)
                decoded = json.loads(base64.b64decode(argv[4]).decode('utf-8'))
                self.assertEqual(decoded, args)
                self.assertTrue(1 <= plan['step_timeout'] <= 120)
                self.assertTrue(plan['step_timeout'] < plan['overall_default'] <= 300)

    def test_plan_rejects_bad_input(self):
        for skill, args in (('nope', {}), ('lint.run', {'extra': 1}), ('lint.run', {}),
                            ('lint.run', 'notadict')):
            with self.subTest(skill=skill):
                with self.assertRaises(phase2_exec.PlanError):
                    phase2_exec.build_phase2_plan(skill, args)

    def test_plan_cli_matches_function(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(TOOLS_DIR, 'phase2_exec.py'),
             'plan', 'lint.run', b64({'path': 'a.py'})],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        plan = json.loads(proc.stdout)['plan']
        self.assertEqual(plan, phase2_exec.build_phase2_plan('lint.run', {'path': 'a.py'}))


# ---------------------------------------------------------------------------
# pkg manifest units (no cache needed)
# ---------------------------------------------------------------------------
class ManifestUnits(WorkspaceCase):
    def test_pkg_list_without_manifest(self):
        envelope = run('pkg.list', {'manager': 'python'}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertFalse(envelope['result']['manifest_present'])

    def test_pkg_list_with_manifest_and_rejections(self):
        self.write('harmonika-packages.json', json.dumps({'packages': [
            {'manager': 'python', 'name': 'humanize', 'version': '4.12.2'},
            {'manager': 'node', 'name': 'left-pad', 'version': '1.3.0'},
        ]}))
        envelope = run('pkg.list', {'manager': 'python'}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertEqual(len(envelope['result']['packages']), 1)
        self.assertTrue(envelope['result']['packages'][0]['allowlisted'])
        envelope = run('pkg.list', {'manager': 'node'}, root=self.root)
        self.assertEqual(envelope['result']['rejected_entries'][0]['name'], 'left-pad')

    def test_manifest_shape_rejected(self):
        for bad in ('[]', '{"packages": {}}', '{"packages": [{"manager": "cargo"}]}',
                    '{"packages": [{"manager": "python", "name": "x"}]}',
                    '{"extra": 1, "packages": []}'):
            self.write('harmonika-packages.json', bad)
            envelope = run('pkg.list', {'manager': 'python'}, root=self.root)
            self.assertFalse(envelope['ok'])
            self.assertEqual(envelope['error']['code'], 'manifest_invalid')

    def test_install_rejects_non_allowlisted(self):
        for args in ({'manager': 'python', 'name': 'requests', 'version': '2.0.0'},
                     {'manager': 'python', 'name': 'humanize', 'version': '9.9.9'},
                     {'manager': 'node', 'name': 'lodash', 'version': '4.17.21'},
                     {'manager': 'cargo', 'name': 'humanize', 'version': '4.12.2'}):
            with self.subTest(args=args):
                envelope = run('pkg.install', args, root=self.root)
                self.assertFalse(envelope['ok'])
                self.assertIn(envelope['error']['code'], ('not_allowlisted', 'invalid_argument'))

    def test_install_rejects_tampered_manifest(self):
        shutil.rmtree(DEPS_PY, ignore_errors=True)
        self.write('harmonika-packages.json', json.dumps({'packages': [
            {'manager': 'node', 'name': 'evil', 'version': '1.0.0'}]}))
        envelope = run('pkg.install',
                       {'manager': 'python', 'name': 'humanize', 'version': '4.12.2'},
                       root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'manifest_invalid')
        self.assertFalse(os.path.exists(os.path.join(DEPS_PY, 'humanize')))


# ---------------------------------------------------------------------------
# Integration: format
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain (black/ruff/mypy/coverage/rope) unavailable')
class FormatIntegration(WorkspaceCase):
    def test_black_reformats_and_hashes(self):
        rel = self.write('a.py', UGLY_PY)
        envelope = run('code.format', {'path': rel}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertEqual(result['language'], 'python')
        self.assertTrue(result['changed'])
        self.assertEqual(self.read(rel), 'def total(amount):\n    return amount + 1\n')
        envelope2 = run('code.format', {'path': rel}, root=self.root)
        self.assertFalse(envelope2['result']['changed'])

    def test_expected_sha256_precondition(self):
        rel = self.write('a.py', UGLY_PY)
        stale = '0' * 64
        envelope = run('code.format', {'path': rel, 'expected_sha256': stale}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'sha256_conflict')
        self.assertEqual(self.read(rel), UGLY_PY)
        good = phase2_tools.sha256_hex(UGLY_PY.encode())
        envelope = run('code.format', {'path': rel, 'expected_sha256': good}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertTrue(envelope['result']['expected_sha256_verified'])

    def test_alias_format_run(self):
        rel = self.write('a.py', UGLY_PY)
        envelope = run('format.run', {'path': rel}, root=self.root)
        self.assertTrue(envelope['ok'])

    @unittest.skipUnless(_node_toolchain(), 'node vendor toolchain unavailable')
    def test_prettier_json(self):
        rel = self.write('data.json', '{"a":1,  "b":[1,2]}')
        envelope = run('code.format', {'path': rel}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        self.assertEqual(envelope['result']['language'], 'json')
        self.assertTrue(envelope['result']['changed'])

    def test_unknown_language_rejected(self):
        rel = self.write('x.rs', 'fn main() {}\n')
        envelope = run('code.format', {'path': rel}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'unsupported_language')


# ---------------------------------------------------------------------------
# Integration: lint
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain unavailable')
class LintIntegration(WorkspaceCase):
    def test_ruff_reports_and_cleans(self):
        dirty = self.write('d.py', 'import os\n\n\ndef g():\n    return 2\n')
        envelope = run('lint.run', {'path': dirty}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertFalse(result['clean'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['findings'][0]['code'], 'F401')
        self.assertEqual(result['findings'][0]['line'], 1)
        self.assertEqual(self.read(dirty), 'import os\n\n\ndef g():\n    return 2\n')
        clean = self.write('c.py', GOOD_PY)
        envelope = run('lint.run', {'path': clean}, root=self.root)
        self.assertTrue(envelope['result']['clean'])
        self.assertEqual(envelope['result']['count'], 0)

    @unittest.skipUnless(_node_toolchain(), 'node unavailable')
    def test_js_syntax_only(self):
        good = self.write('ok.js', 'const x = 1;\n')
        envelope = run('lint.run', {'path': good}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertEqual(envelope['result']['mode'], 'syntax_only')
        self.assertTrue(envelope['result']['clean'])
        bad = self.write('bad.js', 'const x = ;\n')
        envelope = run('lint.run', {'path': bad}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertFalse(envelope['result']['clean'])
        self.assertEqual(envelope['result']['findings'][0]['code'], 'SyntaxError')

    @unittest.skipUnless(_node_toolchain(), 'node unavailable')
    def test_json_parse_check(self):
        good = self.write('v.json', '{ "a": 1 }\n')
        bad = self.write('i.json', '{"a": }')
        envelope = run('lint.run', {'path': good}, root=self.root)
        self.assertTrue(envelope['result']['clean'], envelope)
        self.assertEqual(envelope['result']['mode'], 'format_check')
        envelope = run('lint.run', {'path': bad}, root=self.root)
        self.assertFalse(envelope['result']['clean'])
        self.assertEqual(envelope['result']['findings'][0]['code'], 'ParseError')

    def test_typescript_lint_unsupported(self):
        rel = self.write('x.ts', 'const x: number = 1;\n')
        envelope = run('lint.run', {'path': rel}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'unsupported_language')


# ---------------------------------------------------------------------------
# Integration: type check
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain unavailable')
class TypeCheckPythonIntegration(WorkspaceCase):
    def test_mypy_catches_error(self):
        self.write('bad.py', 'def h(x: int) -> int:\n    return x + "1"\n')
        envelope = run('type.check', {'path': 'bad.py'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        self.assertFalse(envelope['result']['clean'])
        self.assertGreaterEqual(envelope['result']['error_count'], 1)

    def test_mypy_passes_clean(self):
        self.write('good.py', 'def h(x: int) -> int:\n    return x + 1\n')
        envelope = run('type.check', {'path': 'good.py'}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertTrue(envelope['result']['clean'])


@unittest.skipUnless(_node_toolchain(), 'node vendor toolchain unavailable')
class TypeCheckTsIntegration(WorkspaceCase):
    def test_tsc_react_tsx_clean(self):
        self.write('comp.tsx',
                   'import React from "react";\n'
                   'export function Hi({ n }: { n: string }) { return <h1>{n}</h1>; }\n')
        envelope = run('type.check', {'path': 'comp.tsx'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        self.assertTrue(envelope['result']['clean'], envelope['result'])

    def test_tsc_reports_type_errors(self):
        self.write('broken.tsx',
                   'import React from "react";\n'
                   'export function Hi({ n }: { n: string }) { return <h1>{n * 2}</h1>; }\n')
        envelope = run('type.check', {'path': 'broken.tsx'}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertFalse(envelope['result']['clean'])
        self.assertGreaterEqual(envelope['result']['error_count'], 1)

    def test_js_typecheck_unsupported(self):
        self.write('plain.js', 'const x = 1;\n')
        envelope = run('type.check', {'path': 'plain.js'}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'unsupported_language')


# ---------------------------------------------------------------------------
# Integration: build
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain unavailable')
class BuildPythonIntegration(WorkspaceCase):
    def test_zipapp_artifact(self):
        self.write('tool.py', GOOD_PY)
        envelope = run('build.run', {'path': 'tool.py'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        artifact = envelope['result']['artifact']
        self.assertEqual(artifact['path'], 'build/tool.pyz')
        self.assertTrue(os.path.isfile(os.path.join(self.root, 'build', 'tool.pyz')))
        import hashlib
        import zipfile
        with zipfile.ZipFile(os.path.join(self.root, 'build', 'tool.pyz')) as zf:
            self.assertIn('__main__.py', zf.namelist())
        digest = hashlib.sha256(
            open(os.path.join(self.root, 'build', 'tool.pyz'), 'rb').read()).hexdigest()
        self.assertEqual(artifact['sha256'], digest)
        self.assertGreater(artifact['bytes'], 0)

    def test_zipapp_output_still_runs(self):
        self.write('hi.py', 'print("hello-from-zipapp")\n')
        envelope = run('build.run', {'path': 'hi.py'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        proc = subprocess.run([sys.executable, os.path.join(self.root, 'build', 'hi.pyz')],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertIn('hello-from-zipapp', proc.stdout)


@unittest.skipUnless(_node_toolchain(), 'node vendor toolchain unavailable')
class BuildNodeIntegration(WorkspaceCase):
    def test_esbuild_bundle(self):
        self.write('app.js', 'export function price(q){ return q * 2; }\nconsole.log(price(21));\n')
        envelope = run('build.run', {'path': 'app.js'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        artifact = envelope['result']['artifact']
        self.assertEqual(artifact['path'], 'build/app.js')
        built = self.read('build/app.js')
        self.assertIn('price', built)
        import hashlib
        digest = hashlib.sha256(built.encode()).hexdigest()
        self.assertEqual(artifact['sha256'], digest)

    def test_esbuild_uses_vendor_node_path(self):
        self.write('dep.js', 'import React from "react";\n'
                             'export const d = React.createElement("span", null, "ready");\n')
        envelope = run('build.run', {'path': 'dep.js'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)

    @unittest.skipUnless(_pkg_cache_ready(), 'offline package cache unavailable')
    def test_build_resolves_installed_offline_package(self):
        installed=run('pkg.install', {'manager':'node','name':'decimal.js','version':'10.5.0'}, root=self.root)
        self.assertTrue(installed['ok'],installed)
        self.write('dep.js', 'import Decimal from "decimal.js";\nconsole.log(new Decimal(21).times(2).toString());\n')
        result=run('build.run', {'path':'dep.js'}, root=self.root)
        self.assertTrue(result['ok'],result)
        proc=subprocess.run(['node',os.path.join(self.root,result['result']['artifact']['path'])],capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(proc.stdout.strip(),'42')

    def test_oversized_source_rejected_by_workspace_bounds(self):
        self.write('huge.js', 'const pad = "%s";\nconsole.log(pad.length);\n' % ('x' * (700 * 1024)))
        envelope = run('build.run', {'path': 'huge.js'}, root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'file_too_large')

    def test_oversized_artifact_rejected_and_removed(self):
        # Unit-level: shrink the artifact barrier so a legitimate tiny bundle
        # crosses it, proving the artifact path is classified and removed.
        self.write('app.js', 'export const big = "%s";\n' % ('y' * 4096))
        original = phase2_tools.BUILD_ARTIFACT_MAX_BYTES
        phase2_tools.BUILD_ARTIFACT_MAX_BYTES = 1024
        try:
            envelope = run('build.run', {'path': 'app.js'}, root=self.root)
        finally:
            phase2_tools.BUILD_ARTIFACT_MAX_BYTES = original
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'artifact_too_large')
        self.assertFalse(os.path.exists(os.path.join(self.root, 'build', 'app.js')))


# ---------------------------------------------------------------------------
# Integration: pkg.install offline + prepare-env across jobs
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain (pip) unavailable')
@unittest.skipUnless(_pkg_cache_ready(), 'offline pkg cache fixtures unavailable')
class PkgInstallIntegration(WorkspaceCase):
    # One shared workspace across the whole install -> list -> prepare-env
    # sequence, mirroring how one real workspace flows across jobs.
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = cls._tmp.name
        shutil.rmtree(DEPS_PY, ignore_errors=True)
        shutil.rmtree(DEPS_NODE, ignore_errors=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        # Deliberately NOT creating a fresh workspace: the class shares one.
        self.root = type(self).root

    def test_01_python_install_offline_and_importable(self):
        envelope = run('pkg.install',
                       {'manager': 'python', 'name': 'humanize', 'version': '4.12.2'},
                       root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        distinfos = [f for f in os.listdir(DEPS_PY) if f.startswith('humanize-4.12.2')]
        self.assertTrue(distinfos)
        proc = subprocess.run(
            [sys.executable, '-c', 'import humanize; print(humanize.__version__)'],
            env=dict(os.environ, PYTHONPATH=DEPS_PY), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('4.12.2', proc.stdout)
        manifest = json.loads(self.read('harmonika-packages.json'))
        self.assertEqual(manifest['packages'],
                         [{'manager': 'python', 'name': 'humanize', 'version': '4.12.2'}])

    def test_02_node_install_offline(self):
        envelope = run('pkg.install',
                       {'manager': 'node', 'name': 'decimal.js', 'version': '10.5.0'},
                       root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        manifest = json.loads(self.read('harmonika-packages.json'))
        self.assertEqual(len(manifest['packages']), 2)
        pkg_json = os.path.join(DEPS_NODE, 'node_modules', 'decimal.js', 'package.json')
        self.assertEqual(json.load(open(pkg_json))['version'], '10.5.0')

    def test_03_pkg_list_reflects_install(self):
        for manager in ('python', 'node'):
            envelope = run('pkg.list', {'manager': manager}, root=self.root)
            self.assertTrue(envelope['ok'])
            entry = envelope['result']['packages'][0]
            self.assertTrue(entry['installed'], entry)

    def test_04_prepare_env_recovers_across_jobs(self):
        script = os.path.join(TOOLS_DIR, 'phase2_exec.py')
        shutil.rmtree(DEPS_PY, ignore_errors=True)
        shutil.rmtree(DEPS_NODE, ignore_errors=True)
        proc = subprocess.run([sys.executable, script, 'prepare-env', self.root],
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(len(payload['installed']), 2)
        self.assertTrue(os.path.isdir(os.path.join(DEPS_NODE, 'node_modules', 'decimal.js')))
        proc = subprocess.run(
            [sys.executable, '-c', 'import humanize; print(humanize.__version__)'],
            env=dict(os.environ, PYTHONPATH=DEPS_PY), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_05_prepare_env_skips_rejected_and_fails_on_garbage(self):
        script = os.path.join(TOOLS_DIR, 'phase2_exec.py')
        shutil.rmtree(DEPS_PY, ignore_errors=True)
        self.write('harmonika-packages.json', json.dumps({'packages': [
            {'manager': 'python', 'name': 'not-on-list', 'version': '1.0'},
            {'manager': 'python', 'name': 'humanize', 'version': '4.12.2'}]}))
        proc = subprocess.run([sys.executable, script, 'prepare-env', self.root],
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['rejected'][0]['name'], 'not-on-list')
        self.assertEqual(len(payload['installed']), 1)
        self.assertFalse(os.path.exists(os.path.join(DEPS_PY, 'not-on-list')))
        self.write('harmonika-packages.json', 'not json at all')
        proc = subprocess.run([sys.executable, script, 'prepare-env', self.root],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)['error']['code'], 'manifest_invalid')

    def test_06_prepare_env_without_manifest_is_noop(self):
        script = os.path.join(TOOLS_DIR, 'phase2_exec.py')
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        proc = subprocess.run([sys.executable, script, 'prepare-env', empty],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)['manifest'], 'absent')


# ---------------------------------------------------------------------------
# Integration: test.single
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain unavailable')
class TestSinglePython(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.write('calc.py', GOOD_PY)
        self.write('test_calc.py',
                   'import unittest\nfrom calc import total\n\n'
                   'class CalcTest(unittest.TestCase):\n'
                   '    def test_total_adds_one(self):\n'
                   '        self.assertEqual(total(1), 2)\n'
                   '    def test_total_again(self):\n'
                   '        self.assertEqual(total(2), 3)\n')

    def test_runs_exactly_one(self):
        envelope = run('test.single', {'runner': 'python_unittest', 'path': 'test_calc.py',
                                       'name': 'test_calc.CalcTest.test_total_adds_one'},
                       root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertEqual(result['ran'], 1)
        self.assertTrue(result['passed'])
        self.assertIn('test_total_adds_one', result['output'])

    def test_reflected_failure(self):
        self.write('test_bad.py',
                   'import unittest\n'
                   'class BadTest(unittest.TestCase):\n'
                   '    def test_fails(self):\n'
                   '        self.fail("on purpose")\n')
        envelope = run('test.single', {'runner': 'python_unittest', 'path': 'test_bad.py',
                                       'name': 'test_bad.BadTest.test_fails'}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertFalse(envelope['result']['passed'])
        self.assertIn('on purpose', envelope['result']['output'])

    def test_zero_match_is_a_failure(self):
        envelope = run('test.single', {'runner': 'python_unittest', 'path': 'test_calc.py',
                                       'name': 'test_calc.CalcTest.test_missing'},
                       root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'test_not_found')

    def test_wrong_module_prefix_rejected(self):
        envelope = run('test.single', {'runner': 'python_unittest', 'path': 'test_calc.py',
                                       'name': 'other_mod.CalcTest.test_total_adds_one'},
                       root=self.root)
        self.assertEqual(envelope['error']['code'], 'invalid_argument')


@unittest.skipUnless(_node_toolchain(), 'node vendor toolchain unavailable')
class TestSingleNode(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.write('app.test.cjs',
                   "const test = require('node:test');\n"
                   "const assert = require('node:assert/strict');\n"
                   "test('adds numbers', () => { assert.equal(1 + 1, 2); });\n"
                   "test('subs numbers', () => { assert.equal(2 - 1, 1); });\n")

    def test_anchored_single_match(self):
        envelope = run('test.single', {'runner': 'node_test', 'path': 'app.test.cjs',
                                       'name': 'adds numbers'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['pattern'], '^adds\\ numbers$')
        self.assertTrue(result['passed'])

    def test_pattern_cannot_substring_match(self):
        envelope = run('test.single', {'runner': 'node_test', 'path': 'app.test.cjs',
                                       'name': 'numbers'}, root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'test_not_found')

    def test_failing_test_reflected(self):
        self.write('bad.test.cjs',
                   "const test = require('node:test');\n"
                   "const assert = require('node:assert/strict');\n"
                   "test('bad math', () => { assert.equal(1 + 1, 3); });\n")
        envelope = run('test.single', {'runner': 'node_test', 'path': 'bad.test.cjs',
                                       'name': 'bad math'}, root=self.root)
        self.assertTrue(envelope['ok'])
        self.assertFalse(envelope['result']['passed'])

    def test_regex_metachars_are_escaped(self):
        self.write('meta.test.cjs',
                   "const test = require('node:test');\n"
                   "test('a.c (v1)', () => {});\n"
                   "test('abc (v1)', () => {});\n")
        envelope = run('test.single', {'runner': 'node_test', 'path': 'meta.test.cjs',
                                       'name': 'a.c (v1)'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        self.assertEqual(envelope['result']['matched'], 1)


# ---------------------------------------------------------------------------
# Integration: test.coverage
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain (coverage) unavailable')
class CoveragePython(WorkspaceCase):
    def test_real_totals_and_count(self):
        self.write('calc.py', GOOD_PY)
        self.write('test_calc.py',
                   'import unittest\nfrom calc import total\n\n'
                   'class CalcTest(unittest.TestCase):\n'
                   '    def test_total(self):\n'
                   '        self.assertEqual(total(1), 2)\n')
        envelope = run('test.coverage', {'runner': 'python_unittest'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertEqual(result['test_count'], 1)
        self.assertTrue(result['passed'])
        self.assertIsNotNone(result['totals'])
        self.assertEqual(result['totals']['percent_covered_display'], '100')
        self.assertIn('calc.py', result['files'])
        summary = result['files']['calc.py']['summary']
        self.assertEqual(summary['num_statements'], 2)
        self.assertEqual(summary['percent_covered_display'], '100')

    def test_zero_tests_is_failure(self):
        self.write('plain.py', 'x = 1\n')
        envelope = run('test.coverage', {'runner': 'python_unittest'}, root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'no_tests_found')

    def test_failing_tests_reflected_with_coverage(self):
        self.write('test_fail.py',
                   'import unittest\n'
                   'class FailTest(unittest.TestCase):\n'
                   '    def test_no(self):\n'
                   '        self.fail("boom")\n')
        envelope = run('test.coverage', {'runner': 'python_unittest'}, root=self.root)
        self.assertTrue(envelope['ok'])
        result = envelope['result']
        self.assertFalse(result['passed'])
        self.assertEqual(result['test_count'], 1)
        self.assertIsNotNone(result['totals'])


@unittest.skipUnless(_node_toolchain(), 'node vendor toolchain unavailable')
class CoverageNode(WorkspaceCase):
    def test_real_node_coverage(self):
        self.write('app.cjs', 'function add(a, b) { return a + b; }\n'
                              'module.exports = { add };\n')
        self.write('app.test.cjs',
                   "const test = require('node:test');\n"
                   "const assert = require('node:assert/strict');\n"
                   "const { add } = require('./app.cjs');\n"
                   "test('adds', () => { assert.equal(add(1, 2), 3); });\n")
        envelope = run('test.coverage', {'runner': 'node_test'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertEqual(result['test_count'], 1)
        self.assertTrue(result['passed'])
        self.assertIsNotNone(result['totals'])
        self.assertTrue(float(result['totals']['lines']) > 0)
        self.assertTrue(any(f['file'].endswith('app.cjs') for f in result['files']))


# ---------------------------------------------------------------------------
# Integration: rename / extract / dead_code (rope + ruff)
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain (rope/ruff) unavailable')
class RenameIntegration(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.write('calc.py', 'class Calc:\n    def total(self):\n        return 1\n')
        self.write('main.py', 'from calc import Calc\n\nprint(Calc().total())\n')

    def test_cross_file_rename(self):
        envelope = run('code.rename', {'path': 'calc.py', 'line': 2, 'column': 9,
                                       'new_name': 'amount'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertEqual(result['old_name'], 'total')
        self.assertIn('calc.py', result['changed_files'])
        self.assertIn('main.py', result['changed_files'])
        self.assertIn('Calc().amount()', self.read('main.py'))
        self.assertIn('def amount(self)', self.read('calc.py'))
        # alias shares the engine
        self.write('calc2.py', self.read('calc.py'))
        envelope = run('refactor.rename', {'path': 'calc2.py', 'line': 2, 'column': 9,
                                           'new_name': 'sum_total'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        self.assertIn('def sum_total(self)', self.read('calc2.py'))

    def test_import_renamed_too(self):
        envelope = run('code.rename', {'path': 'main.py', 'line': 1, 'column': 18,
                                       'new_name': 'Calc2'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        self.assertIn('from calc import Calc2', self.read('main.py'))
        self.assertIn('Calc2()', self.read('main.py'))

    def test_expected_hash_and_bad_positions(self):
        envelope = run('code.rename', {'path': 'calc.py', 'line': 2, 'column': 9,
                                       'new_name': 'x', 'expected_sha256': '0' * 64},
                       root=self.root)
        self.assertEqual(envelope['error']['code'], 'sha256_conflict')
        envelope = run('code.rename', {'path': 'calc.py', 'line': 99, 'column': 0,
                                       'new_name': 'x'}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'invalid_position')
        envelope = run('code.rename', {'path': 'calc.py', 'line': 2, 'column': 4,
                                       'new_name': 'x'}, root=self.root)
        self.assertIn(envelope['error']['code'], ('invalid_position', 'rename_failed'))
        envelope = run('code.rename', {'path': 'calc.py', 'line': 2, 'column': 4,
                                       'new_name': 'x y'}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'invalid_argument')

    def test_rename_keyword_is_rejected(self):
        envelope = run('code.rename', {'path': 'calc.py', 'line': 2, 'column': 4,
                                       'new_name': 'meth'}, root=self.root)
        self.assertFalse(envelope['ok'])
        self.assertIn(envelope['error']['code'], ('rename_failed', 'invalid_position'))
        self.assertIn('def total', self.read('calc.py'))

    def test_no_ropeproject_created(self):
        envelope = run('code.rename', {'path': 'calc.py', 'line': 2, 'column': 9,
                                       'new_name': 'amount2'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        self.assertFalse(os.path.exists(os.path.join(self.root, '.ropeproject')))


@unittest.skipUnless(_py_toolchain(), 'python toolchain (rope) unavailable')
class ExtractIntegration(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.write('calc.py', GOOD_PY)
        self.write('test_calc.py',
                   'import unittest\nfrom calc import total\n\n'
                   'class CalcTest(unittest.TestCase):\n'
                   '    def test_total(self):\n'
                   '        self.assertEqual(total(1), 2)\n')

    def _run_fixture_test(self):
        proc = subprocess.run([sys.executable, '-m', 'unittest', '-v',
                               'test_calc.CalcTest.test_total'],
                              cwd=self.root, capture_output=True, text=True, timeout=60)
        return proc.returncode == 0

    def test_extract_preserves_behavior(self):
        self.assertTrue(self._run_fixture_test())
        envelope = run('refactor.extract', {'path': 'calc.py', 'start_line': 2, 'end_line': 2,
                                            'name': 'increment'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        self.assertIn('increment', self.read('calc.py'))
        self.assertTrue(self._run_fixture_test(), 'behavior changed after extract')

    def test_bounds_and_names(self):
        envelope = run('refactor.extract', {'path': 'calc.py', 'start_line': 1, 'end_line': 99,
                                            'name': 'm'}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'invalid_position')
        envelope = run('refactor.extract', {'path': 'calc.py', 'start_line': 2, 'end_line': 1,
                                            'name': 'm'}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'invalid_argument')
        envelope = run('refactor.extract', {'path': 'calc.py', 'start_line': 2, 'end_line': 2,
                                            'name': 'not a name'}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'invalid_argument')


@unittest.skipUnless(_py_toolchain(), 'python toolchain (ruff) unavailable')
class DeadCodeIntegration(WorkspaceCase):
    def test_report_only(self):
        self.write('d.py', 'import os\n\n\ndef g():\n    return 2\n')
        envelope = run('refactor.dead_code', {'path': 'd.py'}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertFalse(result['applied'])
        self.assertEqual(result['remaining_count'], 1)
        self.assertEqual(result['findings'][0]['code'], 'F401')
        self.assertFalse(result['changed'])
        self.assertIn('dynamically', result['note'])

    def test_apply_removes_only_the_import(self):
        source = 'import os\n\n\ndef g():\n    return 2\n'
        self.write('d.py', source)
        sha = phase2_tools.sha256_hex(source.encode())
        envelope = run('refactor.dead_code', {'path': 'd.py', 'apply': True,
                                              'expected_sha256': sha}, root=self.root)
        self.assertTrue(envelope['ok'], envelope)
        result = envelope['result']
        self.assertTrue(result['applied'])
        self.assertEqual(result['removed_count'], 1)
        self.assertEqual(result['remaining_count'], 0)
        self.assertTrue(result['changed'])
        after = self.read('d.py')
        self.assertNotIn('import os', after)
        self.assertIn('def g():', after)

    def test_apply_requires_hash(self):
        self.write('d.py', 'import os\n')
        envelope = run('refactor.dead_code', {'path': 'd.py', 'apply': True}, root=self.root)
        self.assertEqual(envelope['error']['code'], 'expected_sha256_required')


# ---------------------------------------------------------------------------
# CLI end-to-end: the exact contract root uses
# ---------------------------------------------------------------------------
@unittest.skipUnless(_py_toolchain(), 'python toolchain unavailable')
class CliEndToEnd(WorkspaceCase):
    def _cli(self, skill, args):
        return subprocess.run(
            [sys.executable, os.path.join(TOOLS_DIR, 'phase2_tools.py'),
             'run', skill, b64(args), '--root', self.root],
            capture_output=True, text=True, timeout=180)

    def test_success_contract(self):
        self.write('a.py', GOOD_PY)
        proc = self._cli('lint.run', {'path': 'a.py'})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertTrue(envelope['ok'])
        self.assertEqual(envelope['result']['engine'], 'ruff')

    @unittest.skipUnless(_py_toolchain(), 'python toolchain unavailable')
    def test_failure_contract(self):
        self.write('a.py', 'import os\n')
        proc = self._cli('refactor.dead_code', {'path': 'a.py', 'apply': True})
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(json.loads(proc.stdout)['ok'])
        self.assertEqual(json.loads(proc.stdout)['error']['code'],
                         'expected_sha256_required')

    def test_stdout_single_json_even_on_boom(self):
        proc = self._cli('code.format', {'path': 'missing.py'})
        self.assertNotEqual(proc.returncode, 0)
        envelope = json.loads(proc.stdout)
        self.assertEqual(envelope['error']['code'], 'not_found')


if __name__ == '__main__':
    unittest.main()

@unittest.skipUnless(_node_toolchain(),'node toolchain unavailable')
class NodeCoverageDirectory(WorkspaceCase):
    def test_directory_path_runs_real_tests(self):
        self.write('nested/example.test.cjs', "const test=require('node:test');const assert=require('node:assert/strict');test('directory test',()=>assert.equal(6*7,42));\n")
        out=run('test.coverage',{'runner':'node_test','path':'nested'},root=self.root)
        self.assertTrue(out['ok'],out);self.assertTrue(out['result']['passed'],out);self.assertEqual(out['result']['test_count'],1)
