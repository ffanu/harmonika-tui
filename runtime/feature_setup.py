#!/usr/bin/env python3
"""Per-user document/SSH/browser setup. Reports mechanics, never raw subprocess logs.

Python packages use exact version locks; npm packages are fetched without npm,
validated against the signed lock's sha512 hashes and extracted without scripts.
Chromium is installed by that verified Playwright CLI. Linux root installs may
provision missing Chromium system libraries via its bounded install-deps command.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib import request, parse, error
from uuid import uuid4
import zipfile

NODE_VERSION = '22.23.4'
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_UNPACKED = 512 * 1024 * 1024


class SetupError(Exception):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def _url(url, hosts):
    u = parse.urlsplit(url)
    if u.scheme != 'https' or u.hostname not in hosts or u.port not in (None, 443) or u.username or u.password or u.fragment:
        raise SetupError('download_origin_denied')
    return url


class RestrictedRedirect(request.HTTPRedirectHandler):
    def __init__(self, hosts): self.hosts = hosts
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, target, hosts, maximum=MAX_ARCHIVE, opener=None):
    _url(url, hosts)
    opener = opener or request.build_opener(RestrictedRedirect(hosts))
    start, total = time.monotonic(), 0
    try:
        with opener.open(url, timeout=20) as response, open(target, 'xb') as output:
            length = response.headers.get('Content-Length')
            if length and (not length.isdigit() or int(length) > maximum):
                raise SetupError('download_size_limit')
            read = getattr(response, 'read1', response.read)
            while True:
                block = read(256 * 1024)
                if not block: break
                total += len(block)
                if total > maximum: raise SetupError('download_size_limit')
                if time.monotonic() - start > 120: raise SetupError('download_timeout', True)
                output.write(block)
    except (error.URLError, TimeoutError, ConnectionError, OSError):
        raise SetupError('download_unavailable', True) from None
    return total


def _safe_name(name):
    p = PurePosixPath(name)
    if not name or not p.parts or '\\' in name or '\0' in name or p.is_absolute() or '..' in p.parts or any(':' in n or n.endswith((' ', '.')) or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?', n, re.I) for n in p.parts):
        raise SetupError('archive_path_denied')
    return p


def _sha(file):
    h = hashlib.sha256()
    with open(file, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def _private_directory(path):
    path = Path(path)
    if path.is_symlink(): raise SetupError('unsafe_runtime_path')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir(): raise SetupError('unsafe_runtime_path')
    return path


def feature_root(root):
    path=Path(root).expanduser().absolute()
    # Older Mac installations use an owner-created profile alias. Follow that
    # one explicit root only when both link and private target belong to this
    # user. Download/extraction directories still reject symlinks.
    if path.is_symlink():
        if os.name!='posix':raise SetupError('unsafe_runtime_path')
        target=path.resolve(strict=True)
        owner=path.lstat();info=target.stat()
        if owner.st_uid!=os.getuid() or info.st_uid!=os.getuid() or not stat.S_ISDIR(info.st_mode) or info.st_mode&0o077:
            raise SetupError('unsafe_runtime_path')
        path=target
    return _private_directory(path)


def _atomic_json(path, value):
    pending = path.with_name('.' + path.name + '-' + uuid4().hex)
    try:
        fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def _environment():
    env = {k: v for k, v in os.environ.items() if not re.search(r'key|token|secret|password', k, re.I)}
    env.update(PYTHONNOUSERSITE='1', PIP_CONFIG_FILE=os.devnull,
               PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INPUT='1', PIP_PROGRESS_BAR='off')
    return env


def _run(command, timeout=60, env=None):
    try:
        result = subprocess.run([str(x) for x in command], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout, env=env or _environment())
    except subprocess.TimeoutExpired:
        raise SetupError('feature_command_timeout', True) from None
    except OSError:
        raise SetupError('feature_command_unavailable') from None
    return result


def _resource(directory, name):
    candidates = []
    if directory:
        candidates += [Path(directory)/'tools'/name, Path(directory)/name]
    candidates += [Path(__file__).resolve().parent/name]
    for file in candidates:
        if file.is_file() and not file.is_symlink(): return file
    raise SetupError('feature_lock_missing')


def _locks(directory):
    docs = _resource(directory, 'documents-requirements.lock')
    intel_mac=platform.system()=='Darwin' and platform.machine().lower() in ('x86_64','amd64')
    ssh = _resource(directory, 'ssh-requirements-darwin-x64.lock' if intel_mac else 'ssh-requirements.lock')
    versions = {}
    for file in (docs, ssh):
        if file.stat().st_size > 65536: raise SetupError('invalid_feature_lock')
        for line in file.read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.lstrip().startswith('#'): continue
            m = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)', line.strip())
            if not m: raise SetupError('invalid_feature_lock')
            old = versions.setdefault(m[1].lower().replace('_', '-'), m[2])
            if old != m[2]: raise SetupError('conflicting_feature_lock')
    return docs, ssh, versions


def _python_probe(executable, versions):
    if not executable.is_file(): return False
    script = "import importlib,importlib.metadata,json,sys,pathlib,re;assert sys.prefix!=sys.base_prefix;assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[2]).resolve();expected=json.loads(sys.argv[1]);actual={re.sub(r'[-_.]+','-',d.metadata['Name']).lower():d.version for d in importlib.metadata.distributions()};[importlib.import_module(n) for n in ['docx','openpyxl','pptx','pypdf','reportlab','PIL','lxml.etree','xlsxwriter','paramiko','telnetlib3','twisted','vncdotool']];assert all(actual.get(re.sub(r'[-_.]+','-',k).lower())==v for k,v in expected.items());print('{\"ok\":true}')"
    result = _run([executable, '-I', '-c', script, json.dumps(versions), str(executable.parent.parent)], timeout=30)
    return result.returncode == 0 and result.stdout.strip() == b'{"ok":true}'


def setup_python(root, directory):
    docs, ssh, versions = _locks(directory)
    target = _private_directory(root/'python-runtime')
    executable = target/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not _python_probe(executable, versions):
        if not executable.is_file():
            interpreter = os.environ.get('HARMONIKA_FEATURE_PYTHON') or sys.executable
            made = _run([interpreter, '-m', 'venv', target], timeout=150)
            if made.returncode: raise SetupError('python_venv_unavailable')
        env = _environment();env['PIP_REQUIRE_VIRTUALENV'] = 'true'
        installed = _run([executable, '-I', '-m', 'pip', 'install', '--disable-pip-version-check',
                          '--no-input', '--progress-bar', 'off', '--only-binary=:all:',
                          '--index-url', 'https://pypi.org/simple', '--timeout', '20', '--retries', '1',
                          '-r', docs, '-r', ssh], timeout=600, env=env)
        if installed.returncode:
            raise SetupError('python_dependencies_unavailable', True)
        if not _python_probe(executable, versions): raise SetupError('python_feature_probe_failed')
    return {'status': 'ready', 'ready': True, 'retryable': False, 'python': str(executable),
            'documents': True, 'ssh': True, 'lock_sha256': hashlib.sha256(docs.read_bytes()+ssh.read_bytes()).hexdigest()}


def npm_entries(lock):
    if lock.get('lockfileVersion') != 3 or not isinstance(lock.get('packages'), dict):
        raise SetupError('invalid_browser_lock')
    entries = []
    for name, item in lock['packages'].items():
        if name == '': continue
        if item.get('optional') and (name.endswith('/fsevents') and sys.platform != 'darwin'):
            continue
        if not re.fullmatch(r'node_modules/(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+', name):
            raise SetupError('invalid_browser_lock')
        url = _url(item.get('resolved', ''), {'registry.npmjs.org'})
        integrity = item.get('integrity', '')
        try:
            expected = base64.b64decode(integrity.removeprefix('sha512-'), validate=True)
        except (ValueError, TypeError): raise SetupError('invalid_browser_integrity') from None
        if not integrity.startswith('sha512-') or len(expected) != 64: raise SetupError('invalid_browser_integrity')
        entries.append((name, item, url, expected))
    if not entries or len(entries) > 32: raise SetupError('invalid_browser_lock')
    return entries


def extract_npm(archive, destination, expected):
    if Path(archive).stat().st_size > 64*1024*1024: raise SetupError('archive_size_limit')
    if not hashlib.sha512(Path(archive).read_bytes()).digest() == expected:
        raise SetupError('browser_package_integrity_failed')
    hashes, names, unpacked = {}, set(), 0
    with tarfile.open(archive, 'r:gz') as tar:
        for i, entry in enumerate(tar):
            p = _safe_name(entry.name)
            if i >= 10000 or entry.name in names: raise SetupError('archive_entry_limit')
            names.add(entry.name)
            if p.parts[0] != 'package': raise SetupError('archive_path_denied')
            if entry.isdir(): continue
            if not entry.isfile() or len(p.parts) < 2: raise SetupError('archive_link_denied')
            unpacked += entry.size
            if entry.size > 64*1024*1024 or unpacked > MAX_UNPACKED: raise SetupError('archive_size_limit')
            relative = PurePosixPath(*p.parts[1:]);file = destination.joinpath(*relative.parts)
            file.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(entry)
            with data, open(file, 'xb') as out: shutil.copyfileobj(data, out)
            hashes[relative.as_posix()] = _sha(file)
    return hashes


def _manifest_healthy(directory, manifest):
    if not isinstance(manifest, dict) or not manifest: return False
    for name, digest in manifest.items():
        p = _safe_name(name);file = directory.joinpath(*p.parts)
        if file.is_symlink() or not file.is_file() or _sha(file) != digest: return False
    return True


def _publish_directory(stage, target):
    old = target.with_name('.' + target.name + '-previous-' + uuid4().hex)
    try:
        if target.exists(): os.replace(target, old)
        os.replace(stage, target)
    except OSError:
        if old.exists() and not target.exists(): os.replace(old, target)
        raise SetupError('feature_runtime_busy', True) from None
    finally:
        if old.exists(): shutil.rmtree(old)


def setup_npm(browser, directory):
    lockfile = _resource(directory, 'browser-package-lock.json')
    if lockfile.stat().st_size > 1024*1024: raise SetupError('invalid_browser_lock')
    lock = json.loads(lockfile.read_text(encoding='utf-8'));entries = npm_entries(lock)
    packagefile = _resource(directory, 'browser-package.json')
    if packagefile.stat().st_size > 65536: raise SetupError('invalid_browser_lock')
    package = json.loads(packagefile.read_text(encoding='utf-8'))
    if package.get('dependencies') != lock['packages'].get('', {}).get('dependencies'):
        raise SetupError('browser_package_lock_mismatch')
    identity = _sha(lockfile);manifest = browser/'module-integrity.json';target = browser/'node_modules'
    if target.is_symlink(): raise SetupError('unsafe_runtime_path')
    try:
        prior = json.loads(manifest.read_text(encoding='utf-8'))
        if prior.get('lock_sha256') == identity and _manifest_healthy(target, prior.get('files')):
            return target/'playwright', identity
    except (OSError, ValueError, SetupError): pass
    with tempfile.TemporaryDirectory(prefix='.browser-modules-', dir=browser) as temp:
        staging = Path(temp)/'node_modules';staging.mkdir();hashes = {}
        for name, item, url, expected in entries:
            archive = Path(temp)/(uuid4().hex+'.tgz');download(url, archive, {'registry.npmjs.org'}, maximum=64*1024*1024)
            package = staging.joinpath(*PurePosixPath(name).parts[1:]);package.mkdir(parents=True)
            for relative, digest in extract_npm(archive, package, expected).items():
                hashes[(PurePosixPath(*PurePosixPath(name).parts[1:])/relative).as_posix()] = digest
            metadata = json.loads((package/'package.json').read_text(encoding='utf-8'))
            if metadata.get('version') != item.get('version'): raise SetupError('browser_package_version_mismatch')
        _publish_directory(staging, target)
        _atomic_json(manifest, {'lock_sha256': identity, 'files': hashes})
    if not (target/'playwright/cli.js').is_file(): raise SetupError('browser_cli_missing')
    return target/'playwright', identity


def _node_version(executable):
    result = _run([executable, '--version'], timeout=15)
    value = result.stdout.decode('ascii', errors='ignore').strip()
    return value[1:] if result.returncode == 0 and re.fullmatch(r'v\d+\.\d+\.\d+', value) else ''


def _node_platform():
    system = {'Darwin': 'darwin', 'Linux': 'linux', 'Windows': 'win'}.get(platform.system())
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x64', 'amd64': 'x64'}.get(platform.machine().lower())
    if not system or not arch or (system == 'win' and arch != 'x64'): raise SetupError('browser_node_platform_unsupported')
    return system+'-'+arch


def extract_node(archive, target, entry_name):
    _safe_name(entry_name);found = False;total = 0
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            entries = z.infolist()
            if len(entries) > 50000: raise SetupError('archive_entry_limit')
            for item in entries:
                _safe_name(item.filename);total += item.file_size
                if total > MAX_UNPACKED: raise SetupError('archive_size_limit')
                if item.filename != entry_name: continue
                if found or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16) or item.file_size > 192*1024*1024:
                    raise SetupError('archive_link_denied')
                with z.open(item) as source, open(target, 'xb') as out: shutil.copyfileobj(source, out)
                found = True
    else:
        with tarfile.open(archive, 'r:gz') as tar:
            for i, item in enumerate(tar):
                _safe_name(item.name);total += item.size
                if i >= 50000: raise SetupError('archive_entry_limit')
                if total > MAX_UNPACKED: raise SetupError('archive_size_limit')
                if item.issym() or item.islnk():
                    # Never extract a link, even when the upstream archive uses
                    # links for npm/corepack. Reject a link masquerading as node.
                    if item.name == entry_name: raise SetupError('archive_link_denied')
                    import posixpath
                    link = posixpath.normpath(posixpath.join(posixpath.dirname(item.name), item.linkname)) if item.issym() else item.linkname
                    resolved = _safe_name(link)
                    if resolved.parts[0] != entry_name.split('/')[0]: raise SetupError('archive_link_denied')
                    continue
                if item.name != entry_name: continue
                if found or not item.isfile() or item.size > 192*1024*1024: raise SetupError('archive_link_denied')
                with tar.extractfile(item) as source, open(target, 'xb') as out: shutil.copyfileobj(source, out)
                found = True
    if not found: raise SetupError('browser_node_missing')
    target.chmod(0o755)


def browser_node(browser):
    supplied = os.environ.get('HARMONIKA_NODE') or shutil.which('node')
    if supplied:
        try: version = _node_version(supplied)
        except SetupError: version = ''
        if version and int(version.split('.')[0]) >= 20: return str(supplied), False
    tag = _node_platform();stem = 'node-v'+NODE_VERSION+'-'+tag
    filename = stem + ('.zip' if tag.startswith('win-') else '.tar.gz')
    target = _private_directory(browser/'node-runtime');exe = target/('node.exe' if tag.startswith('win-') else 'node')
    statefile = target/'verified-node.json'
    try:
        state = json.loads(statefile.read_text())
        if state.get('version') == NODE_VERSION and exe.is_file() and not exe.is_symlink() and _sha(exe) == state.get('sha256') and _node_version(exe) == NODE_VERSION:
            return str(exe), True
    except (OSError, ValueError, SetupError): pass
    with tempfile.TemporaryDirectory(prefix='.node-download-', dir=browser) as temp:
        temp = Path(temp);sums = temp/'SHASUMS256.txt';archive = temp/filename
        download('https://nodejs.org/dist/v'+NODE_VERSION+'/SHASUMS256.txt', sums, {'nodejs.org'}, maximum=256*1024)
        matches = re.findall(r'^([a-f0-9]{64})\s+'+re.escape(filename)+r'$', sums.read_text(), re.MULTILINE)
        if len(matches) != 1: raise SetupError('browser_node_checksum_missing')
        download('https://nodejs.org/dist/v'+NODE_VERSION+'/'+filename, archive, {'nodejs.org'})
        if _sha(archive) != matches[0]: raise SetupError('browser_node_checksum_failed')
        pending = temp/exe.name;extract_node(archive, pending, stem+('/node.exe' if tag.startswith('win-') else '/bin/node'))
        if _node_version(pending) != NODE_VERSION: raise SetupError('browser_node_version_mismatch')
        os.replace(pending, exe)
        _atomic_json(statefile, {'version': NODE_VERSION, 'platform': tag, 'sha256': _sha(exe)})
    return str(exe), True


BROWSER_PROBE = r"""
const fs=require('fs');
(async()=>{let browser;
try{
 const {chromium}=require(process.argv[1]);
 browser=await chromium.launch({headless:true,timeout:20000,args:['--no-sandbox','--disable-gpu','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:160,height:90}});
 await page.setContent('<!doctype html><html><body style="background:#137aca"><p>Harmonika probe</p></body></html>');
 const bytes=await page.screenshot({type:'png',timeout:10000});
 if(bytes.length<100||bytes.toString('hex',0,8)!=='89504e470d0a1a0a')throw Error('invalid_png');
 process.stdout.write(JSON.stringify({ok:true,screenshot_bytes:bytes.length}));
}catch(e){const text=String(e&&e.message||'');const missing=/Host system is missing dependencies|error while loading shared libraries|cannot open shared object file/.test(text);process.stdout.write(JSON.stringify({ok:false,code:missing?'browser_system_libraries_missing':'browser_launch_failed'}));process.exitCode=1;}
finally{if(browser)await browser.close();}})();
"""


def _browser_probe(node, module, env):
    probe = _run([node, '-e', BROWSER_PROBE, module], timeout=60, env=env)
    try: result = json.loads(probe.stdout)
    except (ValueError, UnicodeDecodeError): result = {'ok': False}
    return probe.returncode == 0 and result.get('ok') is True, result


def _install_browser_system_dependencies(node, module, env):
    """Only verified missing libraries authorize an unprivileged/root decision.

    No sudo, package upgrades, global apt configuration or service operations.
    An opted-out/nonroot/unsupported installation stays explicitly degraded.
    """
    choice = os.environ.get('HARMONIKA_INSTALL_SYSTEM_DEPS', 'auto')
    if choice == '0': return False
    privileged = platform.system() == 'Linux' and getattr(os, 'geteuid', lambda: -1)() == 0
    if not privileged:
        if choice == '1': raise SetupError('browser_system_dependencies_privilege_required')
        return False
    if not shutil.which('apt-get'):
        if choice == '1': raise SetupError('browser_system_dependencies_manager_unavailable')
        return False
    # Playwright uses apt-get install. This per-command configuration forbids
    # upgrades of already installed packages, without touching /etc/apt files.
    with tempfile.TemporaryDirectory(prefix='harmonika-browser-deps-') as temporary:
        config = Path(temporary)/'apt.conf'
        config.write_text('APT::Get::Upgrade "false";\nAcquire::Retries "1";\nAcquire::http::Timeout "20";\nAcquire::https::Timeout "20";\n', encoding='utf-8')
        child_env = dict(env, APT_CONFIG=str(config), DEBIAN_FRONTEND='noninteractive',
                         NEEDRESTART_MODE='l', NEEDRESTART_SUSPEND='1')
        installed = _run([node, module/'cli.js', 'install-deps', 'chromium'], timeout=600, env=child_env)
    if installed.returncode: raise SetupError('browser_system_dependencies_install_failed', True)
    return True


def setup_browser(root, directory):
    browser = _private_directory(root/'browser-runtime')
    node, managed = browser_node(browser)
    module, lock_hash = setup_npm(browser, directory)
    cache = _private_directory(browser/'browsers');env = _environment()
    env.update(PLAYWRIGHT_BROWSERS_PATH=str(cache), PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT='20000')
    ready, result = _browser_probe(node, module, env)
    system_dependencies_installed = False
    if not ready:
        # A real missing-library failure already proves Chromium was downloaded.
        # Avoid repeating that download before satisfying its OS dependencies.
        if result.get('code') != 'browser_system_libraries_missing':
            installed = _run([node, module/'cli.js', 'install', 'chromium'], timeout=420, env=env)
            if installed.returncode: raise SetupError('chromium_download_unavailable', True)
            ready, result = _browser_probe(node, module, env)
        if not ready and result.get('code') == 'browser_system_libraries_missing':
            system_dependencies_installed = _install_browser_system_dependencies(node, module, env)
            if system_dependencies_installed: ready, result = _browser_probe(node, module, env)
        if not ready:
            code = result.get('code', 'browser_launch_failed')
            if code not in ['browser_system_libraries_missing', 'browser_launch_failed']: code = 'browser_launch_failed'
            raise SetupError(code)
    return {'status': 'ready', 'ready': True, 'retryable': False, 'module': str(module),
            'browsers_path': str(cache), 'browser_node': node, 'node': node, 'managed_node': managed,
            'lock_sha256': lock_hash, 'screenshot_verified': True,
            'system_dependencies_installed': system_dependencies_installed}


def _failure(exception):
    code = exception.code if isinstance(exception, SetupError) else 'feature_setup_failed'
    retryable = exception.retryable if isinstance(exception, SetupError) else False
    remedies = {'python_venv_unavailable': 'Install Python venv/ensurepip support, then rerun harmonika features setup.',
                'python_dependencies_unavailable': 'Check HTTPS access and supported Python wheels, then rerun harmonika features setup.',
                'browser_system_libraries_missing': 'Install the Chromium system libraries recommended by Playwright for this OS, then rerun harmonika features setup. Linux root with apt-get can install them automatically unless HARMONIKA_INSTALL_SYSTEM_DEPS=0.',
                'browser_system_dependencies_privilege_required': 'HARMONIKA_INSTALL_SYSTEM_DEPS=1 requires Linux root; ask the OS administrator to install Chromium system libraries.',
                'browser_system_dependencies_manager_unavailable': 'Automatic Chromium system dependencies require apt-get; install the OS libraries manually.',
                'browser_system_dependencies_install_failed': 'Check apt-get network, package availability and locks; rerun harmonika features setup. No installed-package upgrades are permitted.',
                'download_unavailable': 'Check HTTPS connectivity, then rerun harmonika features setup.',
                'chromium_download_unavailable': 'Check HTTPS access to the Playwright browser CDN, then rerun harmonika features setup.'}
    return {'status': 'degraded', 'ready': False, 'code': code, 'retryable': retryable,
            'remedy': remedies.get(code, 'Review local feature prerequisites, then rerun harmonika features setup.')}


def ensure_features(root, release_directory=None):
    root = feature_root(root)
    if os.environ.get('HARMONIKA_INSTALL_FEATURES') == '0':
        return {'schema_version': 1, 'status': 'core_only', 'ready': False, 'automatic_install': False,
                'features': {'documents': False, 'ssh': False, 'browser': False}}
    report = {'schema_version': 1, 'automatic_install': True}
    try: report['python'] = setup_python(root, release_directory)
    except Exception as e: report['python'] = _failure(e)
    try: report['browser'] = setup_browser(root, release_directory)
    except Exception as e: report['browser'] = _failure(e)
    report['features'] = {'documents': report['python']['ready'], 'ssh': report['python']['ready'], 'browser': report['browser']['ready']}
    report['ready'] = all(report['features'].values())
    report['status'] = 'ready' if report['ready'] else 'degraded'
    if report['python']['ready']: report['python_runtime'] = report['python']['python']
    if report['browser']['ready']: report['browser_node'] = report['browser']['browser_node']
    _atomic_json(root/'feature-setup-status.json', report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--directory')
    args = parser.parse_args()
    try: report = ensure_features(args.root, args.directory)
    except Exception as e: report = _failure(e)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))


if __name__ == '__main__': main()
