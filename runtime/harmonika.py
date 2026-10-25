#!/usr/bin/python3
"""Harmonika-owned launcher and manual, signed runtime releases. No startup network."""
import base64
from contextlib import contextmanager
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from uuid import uuid4

ROOT = Path(os.environ.get('HARMONIKA_TERMINAL_HOME', str(Path.home()/'.config/harmonika-terminal')))
NODE = os.environ.get('HARMONIKA_NODE') or shutil.which('node') or str(Path.home()/'.local/node/bin/node')
CATALOG = 'https://chat.harmonika.id/assets/harmonika-terminal/stable.json'
RELEASE_RE = re.compile(r'^[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+$')
VERIFY_JS = """const fs=require('fs'),crypto=require('crypto');const e=JSON.parse(fs.readFileSync(0,'utf8'));const p=Buffer.from(e.payload,'base64');if(!crypto.verify(null,p,fs.readFileSync(process.argv[1]),Buffer.from(e.signature,'base64')))process.exit(3);process.stdout.write(p);"""

def digest(file):
    h=hashlib.sha256()
    with open(file,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def read_json(file):return json.loads(Path(file).read_text())

def atomic_state(file,text):
    pending=file.with_name(file.name+'.next-'+uuid4().hex)
    try:
        with open(pending,'x') as out:out.write(text)
        os.chmod(pending,0o600)
        os.replace(pending,file)
    finally:
        if pending.exists():pending.unlink()

def verify_envelope(raw, root=ROOT):
    if len(raw)>1024*1024:raise ValueError('manifest_too_large')
    result=subprocess.run([NODE,'-e',VERIFY_JS,str(root/'trusted-public.pem')],input=raw,capture_output=True,timeout=10)
    if result.returncode:raise ValueError('invalid_release_signature')
    return json.loads(result.stdout)

def bin_name():
    return 'harmonika.exe' if os.name == 'nt' else 'harmonika'

def active(root=ROOT):
    selected=(root/'current').resolve(strict=True)
    if selected.parent!=(root/'versions').resolve() or not RELEASE_RE.fullmatch(selected.name):raise ValueError('invalid_release_pointer')
    manifest=verify_envelope((selected/'release.json').read_bytes(),root)
    if manifest.get('release')!=selected.name:raise ValueError('release_identity_mismatch')
    binary=selected/bin_name()
    if not binary.exists() and (selected/'harmonika').exists():binary=selected/'harmonika'
    if binary.is_symlink() or digest(binary)!=manifest['binary_sha256']:raise ValueError('binary_checksum_mismatch')
    return selected,manifest

def verify_files(directory,manifest):
    for name,info in manifest['files'].items():
        file=directory/name
        if file.is_symlink() or not file.is_file() or digest(file)!=info['sha256']:raise ValueError('runtime_checksum_mismatch:'+name)

def switch(release,root=ROOT):
    if not RELEASE_RE.fullmatch(release):raise ValueError('invalid_release')
    temporary=root/('current.next-'+uuid4().hex)
    try:
        temporary.symlink_to('versions/'+release)
        os.replace(temporary,root/'current')
    finally:
        if temporary.is_symlink():temporary.unlink()

def preflight(directory,manifest):
    verify_files(directory,manifest)
    bpath=directory/bin_name()
    if not bpath.exists() and (directory/'harmonika').exists():bpath=directory/'harmonika'
    result=subprocess.run([str(bpath),'--version'],capture_output=True,text=True,timeout=10)
    if result.returncode or 'v0.98.1' not in result.stdout:raise ValueError('binary_preflight_failed')
    for name in manifest['files']:
        if name.endswith('.cjs'):
            result=subprocess.run([NODE,'--check',str(directory/name)],capture_output=True,timeout=10)
            if result.returncode:raise ValueError('javascript_preflight_failed:'+name)
    python_files=[str(directory/name) for name in manifest['files'] if name.endswith('.py')]
    if python_files:
        result=subprocess.run([sys.executable,'-I','-c',
            "import pathlib,sys; [compile(pathlib.Path(p).read_bytes(),p,'exec') for p in sys.argv[1:]]",
            *python_files],capture_output=True,timeout=15)
        if result.returncode:raise ValueError('python_preflight_failed')
    documents=directory/'skills/harmonika-documents/scripts/documents.py'
    if documents.is_file():
        result=subprocess.run([sys.executable,'-I',str(documents),'capabilities','--detailed'],capture_output=True,text=True,timeout=20)
        if result.returncode:raise ValueError('documents_preflight_failed')
        try:
            probe=json.loads(result.stdout)
            if probe['schema_version']!=1 or not probe['formats']:raise ValueError()
        except (ValueError,KeyError,TypeError):raise ValueError('documents_preflight_failed') from None
    # Launch the real MCP protocol without invoking providers or user sessions.
    check=subprocess.run([NODE,str(directory/'tools/protocol-check.cjs'),str(directory/'tools/mcp-server.cjs')],capture_output=True,text=True,timeout=20)
    if check.returncode:raise ValueError('mcp_preflight_failed')

@contextmanager
def installation_lock(root):
    with open(root/'update.lock','a') as lock:
        if fcntl is not None:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('update_in_progress')
            try:yield
            finally:fcntl.flock(lock,fcntl.LOCK_UN)
        else:
            yield

def install_package(package,root=ROOT):
    with installation_lock(root):return _install_package(package,root)

def _install_package(package,root=ROOT):
    package=Path(package)
    current,previous=active(root)
    if package.stat().st_size>20*1024*1024:raise ValueError('package_too_large')
    with zipfile.ZipFile(package) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or len(names)>512 or 'release.json' not in names:raise ValueError('invalid_package')
        total=0
        for item in archive.infolist():
            parts=Path(item.filename).parts
            if not parts or Path(item.filename).is_absolute() or '..' in parts or '\\' in item.filename or stat.S_ISLNK(item.external_attr>>16):raise ValueError('unsafe_package_path')
            total+=item.file_size
            if total>20*1024*1024:raise ValueError('package_unpacked_too_large')
        manifest=verify_envelope(archive.read('release.json'),root)
        release=manifest.get('release','')
        if not RELEASE_RE.fullmatch(release) or manifest.get('binary_sha256')!=previous['binary_sha256']:raise ValueError('unsupported_binary_change')
        if set(names)!=set(manifest['files'])|{'release.json'}:raise ValueError('package_file_list_mismatch')
        for name in manifest['files']:
            if not (name.startswith('tools/') or name.startswith('skills/') or name=='README.md'):raise ValueError('unsupported_runtime_file')
        target=root/'versions'/release
        if target.exists():raise ValueError('release_already_installed')
        stage=Path(tempfile.mkdtemp(prefix='.stage-',dir=str(root/'versions')))
        try:
            for name in names:
                destination=stage/name;destination.parent.mkdir(parents=True,exist_ok=True)
                with open(destination,'xb') as out:out.write(archive.read(name))
                os.chmod(destination,0o600 if name=='release.json' else 0o644)
            bname=bin_name() if (current/bin_name()).exists() else 'harmonika'
            shutil.copy2(current/bname,stage/bname)
            os.chmod(stage/bname,0o755)
            preflight(stage,manifest)
            stage.rename(target)
            atomic_state(root/'previous',current.name)
            switch(release,root)
        finally:
            if stage.exists():shutil.rmtree(stage)
    return {'ok':True,'release':release,'previous':current.name,'restart_required':True}

def rollback(root=ROOT):
    with installation_lock(root):return _rollback(root)

def _rollback(root=ROOT):
    current,_=active(root)
    release=(root/'previous').read_text().strip()
    if not RELEASE_RE.fullmatch(release) or release==current.name:raise ValueError('no_previous_release')
    target=root/'versions'/release
    manifest=verify_envelope((target/'release.json').read_bytes(),root)
    if target.is_symlink() or manifest.get('release')!=release:raise ValueError('invalid_rollback_target')
    bname=bin_name() if (target/bin_name()).exists() else 'harmonika'
    if digest(target/bname)!=manifest['binary_sha256']:raise ValueError('binary_checksum_mismatch')
    preflight(target,manifest)
    switch(release,root)
    atomic_state(root/'previous',current.name)
    return {'ok':True,'release':release,'restart_required':True}

class SameOrigin(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not newurl.startswith('https://chat.harmonika.id/'):raise ValueError('external_update_redirect_blocked')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def check_update(root=ROOT):
    _,current=active(root)
    with urllib.request.build_opener(SameOrigin()).open(CATALOG,timeout=15) as response:raw=response.read(1024*1024+1)
    newest=verify_envelope(raw,root)
    release=newest.get('release','')
    if not RELEASE_RE.fullmatch(release):raise ValueError('invalid_catalog')
    return {'ok':True,'current':current['release'],'latest':release,'update_available':tuple(map(int,release.split('.')))>tuple(map(int,current['release'].split('.'))),
        'automatic_update':False,'catalog':CATALOG,'package_url':newest.get('package_url')}

def doctor(root=ROOT):
    directory,manifest=active(root)
    preflight(directory,manifest)
    config=read_json(Path.home()/'.config/harmonika/harmonika.json')
    assert config['options']['disable_provider_auto_update'] is True,'provider_auto_update_must_be_disabled'
    assert not config['permissions'].get('allowed_tools'),'global_auto_approvals_detected'
    assert 'harmonika' in config.get('mcp',{}),'mcp_missing'
    documents=subprocess.run([sys.executable,'-I',str(directory/'skills/harmonika-documents/scripts/documents.py'),'capabilities'],capture_output=True,text=True,timeout=20)
    if documents.returncode:raise ValueError('documents_probe_failed')
    document_features=json.loads(documents.stdout)
    browser=subprocess.run([NODE,'-e',"const fs=require('fs'),p=require(process.argv[1]);process.stdout.write(JSON.stringify({chromium:fs.existsSync(p.chromium.executablePath())}));",str(root/'browser-runtime/node_modules/playwright')],capture_output=True,text=True,timeout=10)
    browser_features=json.loads(browser.stdout) if browser.returncode==0 else {}
    return {'ok':True,'release':manifest['release'],'upstream':'0.98.1','binary_verified':True,
        'runtime_verified':True,'mcp_protocol':'ok','skills':len(list((directory/'skills').glob('*/SKILL.md'))),
        'lsp':sorted(config.get('lsp',{})),'documents':document_features,'browser':browser_features,
        'automatic_update':False,'active_sessions_untouched':True}

def run_exec(command, env=None):
    if os.name == 'nt':
        res = subprocess.run(command, env=env)
        sys.exit(res.returncode)
    if env is None:
        os.execv(command[0], command)
    else:
        os.execve(command[0], command, env)

def main():
    args=sys.argv[1:]
    if args==['doctor']:result=doctor()
    elif args==['update','--check']:result=check_update()
    elif len(args)==3 and args[:2]==['update','--apply']:result=install_package(args[2])
    elif args==['rollback']:result=rollback()
    elif args and args[0] in {'network','clipboard'}:
        directory,manifest=active();verify_files(directory,manifest)
        script={'network':'tui_gateway.py','clipboard':'clipboard.py'}[args[0]]
        command=[sys.executable,str(directory/'tools'/script),*args[1:]]
        run_exec(command)
        return
    elif args and args[0]=='state':
        directory,manifest=active();verify_files(directory,manifest)
        command=[sys.executable,str(directory/'tools/state_store.py'),*args[1:]]
        run_exec(command)
        return
    elif args and args[0]=='core':
        directory,manifest=active();verify_files(directory,manifest)
        command=[sys.executable,'-I',str(directory/'tools/core_cli.py'),*args[1:]]
        run_exec(command)
        return
    elif args and args[0]=='mobile':
        directory,manifest=active();verify_files(directory,manifest)
        command=[sys.executable,'-I',str(directory/'tools/mobile_cli.py'),*args[1:]]
        run_exec(command)
        return
    elif args and args[0]=='catalog':
        directory,manifest=active();verify_files(directory,manifest)
        command=[sys.executable,'-I',str(directory/'tools/master_cli.py'),*args[1:]]
        run_exec(command)
        return
    elif args and args[0] in {'builder','build'}:
        directory,manifest=active();verify_files(directory,manifest)
        rest=args[1:] if args[0]=='builder' else args
        command=[sys.executable,'-I',str(directory/'tools/builder_cli.py'),*rest]
        run_exec(command)
        return
    elif args and args[0]=='legal':
        directory,manifest=active();verify_files(directory,manifest)
        command=[sys.executable,'-I',str(directory/'tools/legal_cli.py'),*args[1:]]
        run_exec(command)
        return
    elif args and args[0]=='telco':
        directory,manifest=active();verify_files(directory,manifest)
        command=[sys.executable,'-I',str(directory/'tools/telco_cli.py'),*args[1:]]
        run_exec(command)
        return
    elif args and args[0]=='coding':
        directory,manifest=active();verify_files(directory,manifest)
        command=[sys.executable,'-I',str(directory/'tools/coding_cli.py'),*args[1:]]
        run_exec(command)
        return
    elif args and args[0]=='skills':
        directory,manifest=active();verify_files(directory,manifest)
        command=[sys.executable,'-I',str(directory/'tools/skill_cli.py'),*args[1:]]
        run_exec(command)
        return
    elif args and args[0]=='skill':
        helpers={
            'browser':(NODE,'harmonika-browser-qa/scripts/smoke.cjs'),
            'documents':(sys.executable,'harmonika-documents/scripts/documents.py'),
            'terminal-qa':(sys.executable,'harmonika-terminal-qa/scripts/terminal_qa.py'),
            'async-concurrency':(sys.executable,'harmonika-async-concurrency/scripts/async_runtime.py'),
            'ai-integration':(sys.executable,'harmonika-ai-integration/scripts/stream_protocol.py'),
        }
        if len(args)<2 or args[1] not in helpers:raise ValueError('usage: harmonika skill '+ '|'.join(helpers))
        directory,manifest=active();verify_files(directory,manifest)
        executable,script=helpers[args[1]]
        command=[executable,*(['-I'] if args[1]=='documents' else []),str(directory/'skills'/script),*args[2:]]
        run_exec(command)
        return
    elif args in (['license'],['login']):
        directory,manifest=active();verify_files(directory,manifest)
        lic_script=directory/'tools/license.py'
        if not lic_script.exists():lic_script=Path(__file__).resolve().parent/'license.py'
        run_exec([sys.executable,'-I',str(lic_script),'activate'])
        return
    elif args and args[0] in {'update','doctor'}:raise ValueError('usage: harmonika doctor | update --check | update --apply PACKAGE | rollback')
    else:
        directory,manifest=active()
        verify_files(directory,manifest)
        cfg_dir=os.environ.get('HARMONIKA_GLOBAL_CONFIG',str(Path.home()/'.config/harmonika'))
        data_dir=os.environ.get('HARMONIKA_GLOBAL_DATA',str(Path.home()/'.local/share/harmonika'))
        key_file=Path(cfg_dir)/'harmonika-api-key'
        if not args and sys.stdin.isatty() and not key_file.is_file():
            lic_script=directory/'tools/license.py'
            if not lic_script.exists():lic_script=Path(__file__).resolve().parent/'license.py'
            if lic_script.exists():
                res=subprocess.run([sys.executable,'-I',str(lic_script),'activate'])
                if res.returncode!=0 or not key_file.is_file():
                    sys.exit(res.returncode or 1)
        env=dict(os.environ,
            HARMONIKA_DISABLE_PROVIDER_AUTO_UPDATE='1',
            HARMONIKA_GLOBAL_CONFIG=cfg_dir,
            HARMONIKA_GLOBAL_DATA=data_dir)
        bpath=directory/bin_name()
        if not bpath.exists() and (directory/'harmonika').exists():bpath=directory/'harmonika'
        if os.name == 'nt':
            res = subprocess.run([str(bpath),*args], env=env)
            sys.exit(res.returncode)
        os.execve(str(bpath),['harmonika',*args],env)
        return
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except Exception as error:
        # Only fixed error codes, never print prompts, provider errors or secrets.
        message=str(error) if isinstance(error,(ValueError,AssertionError)) else type(error).__name__
        print(json.dumps({'ok':False,'error':message,'hint':'Instalasi aktif tidak diubah. Periksa harmonika doctor atau rollback bila diperlukan.'}),file=sys.stderr)
        sys.exit(1)
