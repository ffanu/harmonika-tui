#!/usr/bin/env python3
"""Real native-binary installer smoke using only an isolated, synthetic release.

The public binary catalog is authenticated with the production public-key pin.
The current source is then packaged with disposable fixture signing keys. This
does not publish a release, activate a license, or prove all optional features.
No HOME/USERPROFILE override, user configuration, browser, or provider API is used.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
ASSETS = 'https://chat.harmonika.id/assets/harmonika-terminal'
PUBLIC_KEY_SHA256 = '0d07aa02cdde89cb447713c6879747ba0d80bd2b69b7a12d8c618f325a62bea5'
RELEASE = '2099.01.01.1'
VERIFY_JS = "const f=require('fs'),c=require('crypto'),e=JSON.parse(f.readFileSync(0));const p=Buffer.from(e.payload,'base64');if(!c.verify(null,p,f.readFileSync(process.argv[1]),Buffer.from(e.signature,'base64')))process.exit(3);process.stdout.write(p);"
SIGN_JS = "const f=require('fs'),c=require('crypto'),p=f.readFileSync(0);process.stdout.write(JSON.stringify({payload:p.toString('base64'),signature:c.sign(null,p,f.readFileSync(process.argv[1])).toString('base64')}));"


class SmokeFailure(Exception):
    def __init__(self, code, exit_code=None):
        super().__init__(code)
        self.code, self.exit_code = code, exit_code


def require(ok, code):
    if not ok:
        raise SmokeFailure(code)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def platform_key():
    system, arch = platform.system().lower(), platform.machine().lower()
    arch = 'x64' if arch in ('amd64', 'x86_64') else 'arm64' if arch in ('arm64', 'aarch64') else arch
    key = system + '-' + arch
    require(key in {'darwin-arm64', 'darwin-x64', 'linux-arm64', 'linux-x64', 'windows-x64'}, 'unsupported_smoke_platform')
    return key


class SameAssetOrigin(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        asset_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def asset_url(url):
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.netloc == 'chat.harmonika.id'
            and parsed.path.startswith('/assets/harmonika-terminal/'), 'asset_origin_refused')
    return url


def download(url, target, limit):
    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with build_opener(SameAssetOrigin()).open(asset_url(url), timeout=120) as response, target.open('wb') as output:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            require(total <= limit, 'asset_size_limit')
            output.write(block)
    return target


def run(command, *, env, cwd=None, timeout=120, input_bytes=None, check=True, step='subprocess'):
    options = {'env': env, 'cwd': cwd, 'capture_output': True, 'timeout': timeout}
    if input_bytes is None:
        options['stdin'] = subprocess.DEVNULL
    else:
        options['input'] = input_bytes
    try:
        result = subprocess.run(command, **options)
    except subprocess.TimeoutExpired:
        raise SmokeFailure(step + '_timeout') from None
    except OSError:
        raise SmokeFailure(step + '_unavailable') from None
    if check and result.returncode:
        raise SmokeFailure(step + '_failed', result.returncode)
    return result


def verified_payload(path, public_key, node, env):
    result = run([node, '-e', VERIFY_JS, str(public_key)], env=env,
                 input_bytes=path.read_bytes(), step='signature_verification')
    return json.loads(result.stdout)


def snapshot_source(destination):
    ignored = shutil.ignore_patterns('.git', 'dist', 'node_modules', '__pycache__', '.venv', '*.pyc', '.env*', '*.pem', '*.key')
    for name in ('runtime', 'backend', 'skills'):
        shutil.copytree(SOURCE/name, destination/name, ignore=ignored)
    (destination/'docs').mkdir()
    for item in (SOURCE/'docs').iterdir():
        if item.is_file() and item.suffix in ('.md', '.txt', '.json'):
            shutil.copy2(item, destination/'docs'/item.name)
    for name in ('README.md', 'UPSTREAM-LICENSE.md'):
        shutil.copy2(SOURCE/name, destination/name)


def fixture_env(workspace, binary, node):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith('HARMONIKA_') or key in {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'ZAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'}:
            env.pop(key, None)
    profile = workspace/'profil uji Ω ruang'
    env.update({
        'HARMONIKA_TERMINAL_HOME': str(profile/'terminal'),
        'HARMONIKA_GLOBAL_CONFIG': str(profile/'config'),
        'HARMONIKA_CONFIG_DIR': str(profile/'config'),
        'HARMONIKA_GLOBAL_DATA': str(profile/'data'),
        'HARMONIKA_BIN_DIR': str(profile/'bin'),
        'HARMONIKA_DOWNLOADS_DIR': str(profile/'downloads'),
        'HARMONIKA_TOOLS_HOME': str(profile/'shared SDK intentionally absent'),
        'HARMONIKA_BINARY_SOURCE': str(binary),
        'HARMONIKA_NODE': node,
        'HARMONIKA_INSTALL_SSH': '0',
        'HARMONIKA_NONINTERACTIVE': '1',
        'HARMONIKA_RELEASE_NOTES': 'Synthetic cross-platform installer test',
    })
    return env


def without_npm(env, workspace, node):
    restricted = dict(env)
    commands = workspace/'commands without npm'
    commands.mkdir()
    if os.name == 'nt':
        restricted['PATH'] = str(commands) + os.pathsep + str(Path(os.environ['SystemRoot'])/'System32')
    else:
        (commands/'node').symlink_to(node)
        restricted['PATH'] = str(commands)
    require(shutil.which('npm', path=restricted['PATH']) is None, 'npm_visible_during_install')
    require(shutil.which('npm.cmd', path=restricted['PATH']) is None, 'npm_cmd_visible_during_install')
    return restricted


def wrapper_command(wrapper, args):
    if os.name == 'nt':
        # /s strips exactly the outer quotes; preserve the quoted batch path.
        command = '"' + subprocess.list2cmdline([str(wrapper), *args]) + '"'
        return [os.environ.get('ComSpec', 'cmd.exe'), '/d', '/s', '/c', command]
    return [str(wrapper), *args]


def state_snapshot(terminal, config):
    files = {name: (config/name).read_bytes() for name in ('harmonika.json', 'crush.json')}
    return ((terminal/'current.version').read_bytes() if (terminal/'current.version').exists() else None,
            str((terminal/'current').resolve()), files)


def tampered_updates(package, workspace, terminal, node, env):
    with zipfile.ZipFile(package) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    payload = verified_payload(terminal/'current/release.json', terminal/'trusted-public.pem', node, env)
    payload['release'] = '2099.01.01.2'
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    envelope = json.loads(run([node, '-e', SIGN_JS, str(terminal/'signing-private.pem')], env=env,
                              input_bytes=encoded, step='fixture_signing').stdout)
    signature = bytearray(base64.b64decode(envelope['signature']))
    signature[0] ^= 1
    bad_signature = dict(envelope, signature=base64.b64encode(signature).decode())
    paths = []
    for name, release, corrupt in [('bad-signature.zip', bad_signature, False), ('bad-content.zip', envelope, True)]:
        target = workspace/name
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            for filename, content in entries.items():
                if filename == 'release.json':
                    content = json.dumps(release).encode()
                elif corrupt and filename == 'tools/protocol-check.cjs':
                    content += b'\n// synthetic content tampering\n'
                archive.writestr(filename, content)
        paths.append(target)
    return paths


def smoke(workspace, report, expected_platform=None):
    key = platform_key()
    report.update(platform=key, python=platform.python_version(), fixture_release=RELEASE)
    require(expected_platform is None or key == expected_platform, 'runner_platform_mismatch')
    node = shutil.which('node.exe' if os.name == 'nt' else 'node') or shutil.which('node')
    require(node is not None, 'node_missing')
    env = dict(os.environ)
    report['node'] = run([node, '--version'], env=env, step='node_version').stdout.decode().strip()
    trust = workspace/'production trust'
    public = download(ASSETS+'/trusted-public.pem', trust/'trusted-public.pem', 4096)
    require(sha(public) == PUBLIC_KEY_SHA256, 'public_key_pin_mismatch')
    catalog_file = download(ASSETS+'/binaries.json', trust/'binaries.json', 1024*1024)
    catalog = verified_payload(catalog_file, public, node, env)
    entry = catalog['platforms'][key]
    require(isinstance(entry.get('sha256'), str) and len(entry['sha256']) == 64, 'invalid_binary_catalog')
    binary = download(entry['url'], workspace/('native/harmonika.exe' if os.name == 'nt' else 'native/harmonika'), 256*1024*1024)
    require(sha(binary) == entry['sha256'], 'native_binary_checksum_mismatch')
    binary.chmod(0o700)
    version = run([str(binary), '--version'], env=env, step='native_binary_version')
    require(b'v0.98.1' in version.stdout, 'native_binary_version_mismatch')
    report.update(public_catalog_verified=True, native_binary_sha256=entry['sha256'], native_binary_runs=True)
    source = workspace/'source'
    snapshot_source(source)
    report['source_sha256'] = {
        name: sha(source/name) for name in (
            'runtime/install.py', 'runtime/harmonika.py', 'runtime/package-release.py',
            'runtime/protocol-check.cjs', 'runtime/tools/ssh-protocol-check.cjs', 'runtime/tools/paths.cjs',
            'runtime/bundle-dependencies.py', 'runtime/dependencies/package-lock.json')}
    env = fixture_env(workspace, binary, node)
    terminal = Path(env['HARMONIKA_TERMINAL_HOME'])
    config = Path(env['HARMONIKA_GLOBAL_CONFIG'])
    run([sys.executable, str(source/'runtime/package-release.py'), RELEASE], env=env, cwd=source,
        timeout=600, step='current_source_fixture_build')
    package = source/'dist'/('harmonika-terminal-'+RELEASE+'.zip')
    report['fixture_package_sha256'] = sha(package)
    with zipfile.ZipFile(package) as archive:
        require('tools/vendor/sdk/client/index.js' in archive.namelist(), 'bundled_sdk_missing')
        require('tools/vendor/ajv2020.cjs' in archive.namelist(), 'bundled_validator_missing')
        require(not any('/node_modules/' in name for name in archive.namelist()), 'node_modules_shipped')
        report['package_file_count'] = len(archive.namelist())
    restricted = without_npm(env, workspace, node)
    run([sys.executable, str(source/'runtime/install.py'), str(package)], env=restricted, cwd=source,
        timeout=240, step='fresh_install_without_npm')
    require(not Path(env['HARMONIKA_TOOLS_HOME']).exists(), 'shared_sdk_fallback_used')
    require((terminal/'current/tools/mcp-server.cjs').is_file(), 'runtime_pointer_unusable')
    for checker, server in [('protocol-check.cjs', 'mcp-server.cjs'), ('ssh-protocol-check.cjs', 'ssh-mcp.cjs')]:
        run([node, str(terminal/'current/tools'/checker), str(terminal/'current/tools'/server)],
            env=restricted, timeout=30, step=server.replace('.cjs', '') + '_discovery')
    wrapper = Path(env['HARMONIKA_BIN_DIR'])/('harmonika.cmd' if os.name == 'nt' else 'harmonika')
    require(wrapper.is_file(), 'installed_wrapper_missing')
    doctor = run(wrapper_command(wrapper, ['doctor']), env=restricted, timeout=120, step='installed_wrapper_doctor')
    try:
        status = json.loads(doctor.stdout)
    except (ValueError, UnicodeError):
        raise SmokeFailure('doctor_invalid_json') from None
    require(status.get('ok') is True and status.get('mcp_protocol') == 'ok' and status.get('binary_verified') is True,
            'doctor_not_healthy')
    report.update(fresh_install_without_npm=True, bundled_mcp_healthy=True, installed_wrapper_works=True,
                  ssh_mcp_discovery_healthy=True, shared_sdk_used=False, license_or_provider_api_called=False)
    before = state_snapshot(terminal, config)
    negative = tampered_updates(package, workspace, terminal, node, restricted)
    for tampered, expected in zip(negative, (b'invalid_release_signature', b'runtime_checksum_mismatch')):
        result = run(wrapper_command(wrapper, ['update', '--apply', str(tampered)]), env=restricted,
                     timeout=120, check=False, step='tampered_update')
        require(result.returncode != 0 and expected in result.stdout+result.stderr, 'tampered_update_not_refused')
        require(state_snapshot(terminal, config) == before, 'failed_update_changed_installation')
    require(not (config/'harmonika-api-key').exists(), 'unexpected_license_key')
    report.update(invalid_signature_refused=True, tampered_content_refused=True,
                  failed_updates_preserve_config_and_pointer=True, passed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-root', type=Path)
    parser.add_argument('--expected-platform', choices=['darwin-arm64', 'darwin-x64', 'linux-arm64', 'linux-x64', 'windows-x64'])
    args = parser.parse_args()
    work_root = args.work_root
    if work_root is None and platform.system() == 'Darwin':
        work_root = Path.home()/'M5/penting-harmonika-installer-smoke'
    if work_root is not None:
        work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {'schema_version': 1, 'passed': False, 'synthetic_fixture_only': True,
              'public_release_published': False, 'optional_ssh_execution_tested': False,
              'user_home_overridden': False}
    began = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='harmonika installer smoke ', dir=work_root) as temp:
            smoke(Path(temp), report, args.expected_platform)
    except SmokeFailure as error:
        report.update(passed=False, error=error.code, exit_code=error.exit_code)
    except Exception as error:
        report.update(passed=False, error='smoke_exception', error_type=type(error).__name__)
    report['duration_seconds'] = round(time.monotonic()-began, 2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
