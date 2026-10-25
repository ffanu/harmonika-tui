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

def _find_node():
    env_node = os.environ.get('HARMONIKA_NODE')
    if env_node and Path(env_node).is_file():
        return env_node
    saved = ROOT / 'node.path'
    if saved.is_file():
        try:
            p = saved.read_text().strip()
            if p and Path(p).is_file():
                return p
        except Exception:
            pass
    which_node = shutil.which('node.exe' if os.name == 'nt' else 'node') or shutil.which('node')
    if which_node:
        return which_node
    if os.name == 'nt':
        for candidate in [
            Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'nodejs/node.exe',
            Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'nodejs/node.exe',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/nodejs/node.exe',
            Path.home() / 'AppData/Local/Programs/nodejs/node.exe',
        ]:
            if candidate.is_file():
                return str(candidate)
    return str(Path.home()/'.local/node/bin/node')

NODE = _find_node()
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

import platform
import uuid as _uuid

def device_id(cfg_dir=None):
    cdir=Path(cfg_dir) if cfg_dir else (Path.home()/'.config/harmonika')
    id_file=cdir/'device-id'
    try:
        if id_file.is_file():
            saved=id_file.read_text().strip()
            if re.fullmatch(r'dev_[a-f0-9]{32}',saved):return saved
    except Exception:pass
    raw=f"{platform.node()}|{platform.system()}|{platform.machine()}|{_uuid.getnode()}"
    computed='dev_'+hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]
    try:
        cdir.mkdir(parents=True,exist_ok=True)
        id_file.write_text(computed+'\n')
    except Exception:pass
    return computed

def device_name():
    node=platform.node() or 'terminal'
    return f"{node} ({platform.system()}-{platform.machine()})"[:72]

def bin_name():
    return 'harmonika.exe' if os.name == 'nt' else 'harmonika'

def active(root=ROOT):
    selected=None
    ver_file=root/'current.version'
    if os.name=='nt' and ver_file.is_file():
        rel_name=ver_file.read_text().strip()
        if RELEASE_RE.fullmatch(rel_name) and (root/'versions'/rel_name).is_dir():
            selected=(root/'versions'/rel_name).resolve(strict=True)
    if selected is None:
        cur=root/'current'
        if os.name=='nt' and cur.is_file() and not cur.is_symlink():
            rel_name=cur.read_text().strip()
            if RELEASE_RE.fullmatch(rel_name) and (root/'versions'/rel_name).is_dir():
                selected=(root/'versions'/rel_name).resolve(strict=True)
        if selected is None:
            selected=cur.resolve(strict=True)
    if selected.parent!=(root/'versions').resolve() or not RELEASE_RE.fullmatch(selected.name):raise ValueError('invalid_release_pointer')
    manifest=verify_envelope((selected/'release.json').read_bytes(),root)
    if manifest.get('release')!=selected.name:raise ValueError('release_identity_mismatch')
    binary=selected/bin_name()
    if not binary.exists() and (selected/'harmonika').exists():binary=selected/'harmonika'
    expected_sha=manifest['binary_sha256']
    plat_sha_file=selected/'binary.sha256'
    if os.name=='nt' and plat_sha_file.is_file():
        expected_sha=plat_sha_file.read_text().strip()
    if binary.is_symlink() or digest(binary)!=expected_sha:raise ValueError('binary_checksum_mismatch')
    return selected,manifest

def verify_files(directory,manifest):
    for name,info in manifest['files'].items():
        file=directory/name
        if file.is_symlink() or not file.is_file() or digest(file)!=info['sha256']:raise ValueError('runtime_checksum_mismatch:'+name)

def switch(release,root=ROOT):
    if not RELEASE_RE.fullmatch(release):raise ValueError('invalid_release')
    if os.name=='nt':
        atomic_state(root/'current.version',release)
        cur=root/'current'
        try:
            if cur.is_symlink() or cur.is_file():cur.unlink()
            elif cur.is_dir():
                subprocess.run(['cmd','/c','rmdir',str(cur)],capture_output=True)
            subprocess.run(['cmd','/c','mklink','/J',str(cur),str(root/'versions'/release)],capture_output=True)
        except Exception:
            pass
        return
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
            if os.name=='nt':
                (stage/'binary.sha256').write_text(digest(stage/bname))
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

def ensure_harmonika_downloads(cfg_dir=None):
    cdir=Path(cfg_dir) if cfg_dir else (Path.home()/'.config/harmonika')
    dl_root=Path.home()/'Downloads'/'harmonika-ai'
    subfolders=('gambar','document','zip')
    access_dir=cdir/'project-access'
    try:
        dl_root.mkdir(parents=True,exist_ok=True)
        access_dir.mkdir(parents=True,exist_ok=True)
        for folder in [dl_root,*[dl_root/s for s in subfolders]]:
            folder.mkdir(parents=True,exist_ok=True)
            real_str=str(folder.resolve())
            rec_file=access_dir/(hashlib.sha256(real_str.encode('utf-8')).hexdigest()+'.json')
            if not rec_file.exists():
                rec_file.write_text(json.dumps({'root':real_str}))
        guide_file=cdir/'HARMONIKA.md'
        dl_abs=str(dl_root.resolve())
        gambar_abs=str((dl_root/'gambar').resolve())
        doc_abs=str((dl_root/'document').resolve())
        zip_abs=str((dl_root/'zip').resolve())
        guide_text=(
            "# Aturan Penyimpanan File Otomatis Harmonika AI\n\n"
            "1. **Sesuai Permintaan User (Custom Folder):**\n"
            "   Jika pengguna meminta secara spesifik untuk membuat folder baru (di `Downloads` atau `Projects`) atau menyimpan di folder proyek tertentu, ikuti permintaan pengguna dan gunakan tool `create_directory` bila membuat folder proyek baru.\n\n"
            "2. **Inisiatif Penyimpanan Otomatis Berdasarkan Jenis File (`Downloads/harmonika-ai`):**\n"
            "   Jika pengguna meminta membuat/mengunduh/menyimpan file tanpa menentukan folder tujuan spesifik, atau meminta disimpan di Downloads, simpan secara otomatis dan terfokus di dalam folder khusus berikut yang sudah siap pakai (auto-created):\n"
            f"   - **Folder Utama Harmonika AI:** `{dl_abs}`\n"
            f"   - **File Gambar** (`.png`, `.jpg`, `.jpeg`, `.webp`, `.svg`, `.gif` — hasil generate gambar, desain, poster, screenshot): simpan di `{gambar_abs}`\n"
            f"   - **File Dokumen** (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.csv`, `.txt`, `.md`, `.html` — hasil dokumen, laporan, tabel, presentasi): simpan di `{doc_abs}`\n"
            f"   - **File Arsip / Bundle** (`.zip`, `.tar.gz`, `.tgz`, `.7z` — hasil kompresi atau paket kode/file): simpan di `{zip_abs}`\n"
        )
        guide_file.write_text(guide_text,encoding='utf-8')
    except Exception:
        pass
    return dl_root

def prompt_startup_update(root=ROOT,force_output=False):
    try:
        _,current=active(root)
        with urllib.request.build_opener(SameOrigin()).open(CATALOG,timeout=4) as response:
            raw=response.read(1024*1024+1)
        newest=verify_envelope(raw,root)
        release=newest.get('release','')
        if not RELEASE_RE.fullmatch(release):
            return None
        cur_ver=current['release']
        is_newer=tuple(map(int,release.split('.')))>tuple(map(int,cur_ver.split('.')))
        pkg_url=newest.get('package_url','')
        if not is_newer or not pkg_url:
            if force_output:
                print(f"\033[38;2;63;185;80m✓ Harmonika AI sudah menggunakan versi terbaru ({cur_ver}).\033[0m")
            return {'ok':True,'updated':False,'current':cur_ver,'latest':release}
        O='\033[38;2;255;123;0m';B='\033[1m';G='\033[38;2;63;185;80m';C='\033[38;2;88;166;255m';R='\033[0m'
        print(f"\n{O}╭──────────────────────────────────────────────────────────────────────────╮{R}")
        print(f"{O}│{R}  {B}{O}HARMONIKA AI — PEMBARUAN TERBARU TERSEDIA{R}                               {O}│{R}")
        print(f"{O}│{R}                                                                          {O}│{R}")
        print(f"{O}│{R}  Versi Saat Ini : {C}{cur_ver:<54}{R} {O}│{R}")
        print(f"{O}│{R}  Versi Terbaru  : {G}{B}{release:<54}{R} {O}│{R}")
        print(f"{O}│{R}                                                                          {O}│{R}")
        print(f"{O}│{R}  Pilih tindakan pembaruan:                                               {O}│{R}")
        print(f"{O}│{R}    {B}[Y] Yes{R}   — Update otomatis sekarang ke versi terbaru                 {O}│{R}")
        print(f"{O}│{R}    {B}[N] No{R}    — Tidak sekarang, langsung buka Harmonika AI                {O}│{R}")
        print(f"{O}│{R}    {B}[L] Later{R} — Ingatkan lagi nanti saat membuka Harmonika AI             {O}│{R}")
        print(f"{O}╰──────────────────────────────────────────────────────────────────────────╯{R}")
        try:
            ans=input(f"{O}Pilihan Anda [Y/n/l] (Enter = Yes): {R}").strip().lower()
        except (EOFError,KeyboardInterrupt):
            print()
            ans='l'
        if ans in ('','y','yes','ya'):
            print(f"{O}↻ Mengunduh dan memasang pembaruan Harmonika AI v{release}...{R}")
            tmp_pkg=root/('auto-update-'+release+'.zip')
            try:
                with urllib.request.build_opener(SameOrigin()).open(pkg_url,timeout=120) as r, open(tmp_pkg,'wb') as out:
                    shutil.copyfileobj(r,out)
                if newest.get('package_sha256') and digest(tmp_pkg)!=newest['package_sha256']:
                    raise ValueError('package_checksum_mismatch')
                install_package(tmp_pkg,root)
                new_dir,_=active(root)
                if (new_dir/'tools/harmonika.py').is_file():
                    atomic_state(root/'launcher.py',(new_dir/'tools/harmonika.py').read_text())
                print(f"{G}✓ Berhasil diperbarui ke Harmonika AI v{release}!{R}\n")
                return {'ok':True,'updated':True,'release':release,'previous':cur_ver}
            finally:
                tmp_pkg.unlink(missing_ok=True)
        elif ans in ('n','no','tidak'):
            print(f"{C}ℹ Pembaruan dilewati untuk sesi ini (tetap standby saat Anda memanggil harmonika kembali).{R}\n")
            return {'ok':True,'updated':False,'choice':'no','current':cur_ver,'latest':release}
        else:
            print(f"{C}ℹ Pembaruan ditunda ke sesi berikutnya (Later). Membuka Harmonika AI...{R}\n")
            return {'ok':True,'updated':False,'choice':'later','current':cur_ver,'latest':release}
    except Exception as exc:
        if force_output:
            print(f"Gagal memeriksa/memasang pembaruan: {exc}",file=sys.stderr)
        return None

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
    ensure_harmonika_downloads(os.environ.get('HARMONIKA_GLOBAL_CONFIG',str(Path.home()/'.config/harmonika')))
    if args==['doctor']:result=doctor()
    elif args==['update']:
        res=prompt_startup_update(ROOT,force_output=True)
        result=res or check_update()
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
    elif args and args[0] in ('license','login'):
        directory,manifest=active();verify_files(directory,manifest)
        lic_script=directory/'tools/license.py'
        if not lic_script.exists():lic_script=Path(__file__).resolve().parent/'license.py'
        extra=args[1:] if len(args)>1 else []
        run_exec([sys.executable,'-I',str(lic_script),'activate',*extra])
        return
    elif args and args[0] in {'update','doctor'}:raise ValueError('usage: harmonika doctor | update --check | update --apply PACKAGE | rollback')
    else:
        directory,manifest=active()
        verify_files(directory,manifest)
        cfg_dir=os.environ.get('HARMONIKA_GLOBAL_CONFIG',str(Path.home()/'.config/harmonika'))
        data_dir=os.environ.get('HARMONIKA_GLOBAL_DATA',str(Path.home()/'.local/share/harmonika'))
        dl_root=ensure_harmonika_downloads(cfg_dir)
        key_file=Path(cfg_dir)/'harmonika-api-key'
        if not args and sys.stdin.isatty() and not key_file.is_file():
            lic_script=directory/'tools/license.py'
            if not lic_script.exists():lic_script=Path(__file__).resolve().parent/'license.py'
            if lic_script.exists():
                res=subprocess.run([sys.executable,'-I',str(lic_script),'activate'])
                if res.returncode!=0 or not key_file.is_file():
                    sys.exit(res.returncode or 1)
        if not args and sys.stdin.isatty() and sys.stdout.isatty():
            upd=prompt_startup_update(ROOT,force_output=False)
            if upd and upd.get('updated'):
                directory,manifest=active()
                verify_files(directory,manifest)
        api_key_val=''
        if key_file.is_file():
            try:api_key_val=key_file.read_text().strip()
            except Exception:pass
        dev_id=device_id(cfg_dir)
        dev_nm=device_name()
        # Ensure crush.json, harmonika.json, data_dir/crush.json, and themes/harmonika-dark.json stay in sync
        try:
            themes_dir=Path(cfg_dir)/'themes'
            themes_dir.mkdir(parents=True,exist_ok=True)
            theme_file=themes_dir/'harmonika-dark.json'
            if not theme_file.is_file():
                orange_dark_theme={
                    'base':'charmtone-panther','primary':'#ff7b00','secondary':'#ff9e00','accent':'#ffa657','on_primary':'#0d1117',
                    'bg_base':'#0d1117','bg_less_visible':'#161b22','bg_least_visible':'#21262d','bg_most_visible':'#30363d',
                    'fg_base':'#f0f6fc','fg_subtle':'#c9d1d9','fg_more_subtle':'#8b949e','fg_most_subtle':'#6e7681','button_subtle':'#21262d',
                    'info':'#58a6ff','info_more_subtle':'#1f6feb','info_most_subtle':'#0d419d',
                    'success':'#3fb950','success_more_subtle':'#238636','success_most_subtle':'#12261e',
                    'warning':'#ff9e00','warning_subtle':'#9e6a03','error':'#f85149','plan':'#ff7b00','plan_more_subtle':'#3d2200',
                    'diff_insert_fg':'#7ee787','diff_insert_code_bg':'#12261e','diff_insert_gutter_bg':'#19362a',
                    'diff_delete_fg':'#ff7b72','diff_delete_code_bg':'#2d1619','diff_delete_gutter_bg':'#3d1d21',
                    'ansi_black':'#484f58','ansi_red':'#ff7b72','ansi_green':'#3fb950','ansi_yellow':'#ffa657',
                    'ansi_blue':'#58a6ff','ansi_magenta':'#d2a8ff','ansi_cyan':'#39c5cf','ansi_white':'#b1bac4',
                    'ansi_bright_black':'#6e7681','ansi_bright_red':'#ffa198','ansi_bright_green':'#7ee787','ansi_bright_yellow':'#ffdf5d',
                    'ansi_bright_blue':'#79c0ff','ansi_bright_magenta':'#e2c5ff','ansi_bright_cyan':'#56d4dd','ansi_bright_white':'#ffffff'
                }
                theme_file.write_text(json.dumps(orange_dark_theme,indent=2)+'\n')
            Path(data_dir).mkdir(parents=True,exist_ok=True)
            data_crush=Path(data_dir)/'crush.json'
            if not data_crush.is_file():
                data_crush.write_text('{"options":{"tui":{"transparent":false,"active_theme":"harmonika-dark"}}}\n')
            h_cfg_path=Path(cfg_dir)/'harmonika.json'
            c_cfg_path=Path(cfg_dir)/'crush.json'
            if h_cfg_path.is_file():
                cfg_obj=json.loads(h_cfg_path.read_text())
                prov=cfg_obj.setdefault('providers',{}).setdefault('harmonika',{})
                if api_key_val and os.name=='nt':
                    prov['api_key']=api_key_val
                extra_hdrs=prov.setdefault('extra_headers',{})
                extra_hdrs['X-Harmonika-Device-ID']=dev_id
                extra_hdrs['X-Harmonika-Device-Name']=dev_nm
                cfg_obj.setdefault('options',{}).setdefault('tui',{})['active_theme']='harmonika-dark'
                rendered_cfg=json.dumps(cfg_obj,indent=2)+'\n'
                h_cfg_path.write_text(rendered_cfg)
                c_cfg_path.write_text(rendered_cfg)
        except Exception:
            pass
        env=dict(os.environ,
            HARMONIKA_DISABLE_PROVIDER_AUTO_UPDATE='1',
            CRUSH_DISABLE_PROVIDER_AUTO_UPDATE='1',
            HARMONIKA_GLOBAL_CONFIG=cfg_dir,
            CRUSH_GLOBAL_CONFIG=cfg_dir,
            HARMONIKA_GLOBAL_DATA=data_dir,
            CRUSH_GLOBAL_DATA=data_dir,
            HARMONIKA_DEVICE_ID=dev_id,
            HARMONIKA_DEVICE_NAME=dev_nm,
            HARMONIKA_DOWNLOADS_DIR=str(dl_root),
            HARMONIKA_IMAGES_DIR=str(dl_root/'gambar'),
            HARMONIKA_DOCUMENTS_DIR=str(dl_root/'document'),
            HARMONIKA_ZIP_DIR=str(dl_root/'zip'),
            **({'HARMONIKA_API_KEY':api_key_val} if api_key_val else {}))
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
