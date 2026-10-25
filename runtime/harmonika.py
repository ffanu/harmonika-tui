#!/usr/bin/python3
"""Harmonika-owned launcher and manual, signed runtime releases. No startup network."""
import base64
from contextlib import contextmanager
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import importlib.util
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

def platform_id():
    arch=platform.machine().lower()
    arch='x64' if arch in ('x86_64','amd64') else 'arm64' if arch in ('aarch64','arm64') else arch
    return platform.system().lower()+'-'+arch

def expected_binary_sha(manifest,platform_key=None):
    """New releases bind every platform checksum inside the signed envelope."""
    if 'binary_hashes' in manifest:
        hashes=manifest['binary_hashes']
        if not isinstance(hashes,dict):raise ValueError('invalid_platform_checksums')
        value=hashes.get(platform_key or platform_id())
        if value is None:raise ValueError('platform_binary_unavailable')
    else:value=manifest.get('binary_sha256')
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('invalid_binary_checksum')
    return value

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
    expected_sha=expected_binary_sha(manifest)
    plat_sha_file=selected/'binary.sha256'
    if 'binary_hashes' not in manifest and os.name=='nt' and plat_sha_file.is_file():
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
    if 'binary_hashes' in manifest and digest(bpath)!=expected_binary_sha(manifest):raise ValueError('binary_checksum_mismatch')
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

def install_package(package,root=ROOT,*,binary_source=None):
    with installation_lock(root):return _install_package(package,root,binary_source=binary_source)

def _install_package(package,root=ROOT,*,binary_source=None):
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
        if not RELEASE_RE.fullmatch(release):raise ValueError('invalid_release')
        expected=expected_binary_sha(manifest)
        if binary_source is None:
            if expected!=expected_binary_sha(previous):raise ValueError('unsupported_binary_change')
        elif 'binary_hashes' not in manifest or Path(binary_source).is_symlink() or digest(binary_source)!=expected:
            raise ValueError('platform_binary_checksum_mismatch')
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
            shutil.copy2(binary_source or current/bname,stage/bname)
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
    if digest(target/bname)!=expected_binary_sha(manifest):raise ValueError('binary_checksum_mismatch')
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

HARMONIKA_THEMES = {
    'harmonika-dark': {
        'base':'charmtone-panther','primary':'#ff7b00','secondary':'#ff9e00','accent':'#ffa657','on_primary':'#0b0e14',
        'bg_base':'#0b0e14','bg_less_visible':'#131822','bg_least_visible':'#1c2331','bg_most_visible':'#283246',
        'fg_base':'#f0f6fc','fg_subtle':'#d1d7e0','fg_more_subtle':'#9198a1','fg_most_subtle':'#6e7681','button_subtle':'#1c2331',
        'info':'#58a6ff','info_more_subtle':'#1f6feb','info_most_subtle':'#0d419d',
        'success':'#3fb950','success_more_subtle':'#238636','success_most_subtle':'#12261e',
        'warning':'#ff9e00','warning_subtle':'#9e6a03','error':'#f85149','plan':'#ff7b00','plan_more_subtle':'#3d2200',
        'diff_insert_fg':'#7ee787','diff_insert_code_bg':'#12261e','diff_insert_gutter_bg':'#19362a',
        'diff_delete_fg':'#ff7b72','diff_delete_code_bg':'#2d1619','diff_delete_gutter_bg':'#3d1d21',
        'ansi_black':'#484f58','ansi_red':'#ff7b72','ansi_green':'#3fb950','ansi_yellow':'#ffa657',
        'ansi_blue':'#58a6ff','ansi_magenta':'#d2a8ff','ansi_cyan':'#39c5cf','ansi_white':'#b1bac4',
        'ansi_bright_black':'#6e7681','ansi_bright_red':'#ffa198','ansi_bright_green':'#7ee787','ansi_bright_yellow':'#ffdf5d',
        'ansi_bright_blue':'#79c0ff','ansi_bright_magenta':'#e2c5ff','ansi_bright_cyan':'#56d4dd','ansi_bright_white':'#ffffff'
    },
    'harmonika-cyber': {
        'base':'charmtone-panther','primary':'#00e5ff','secondary':'#ff7b00','accent':'#7c4dff','on_primary':'#080b12',
        'bg_base':'#080b12','bg_less_visible':'#0f1523','bg_least_visible':'#182236','bg_most_visible':'#24324f',
        'fg_base':'#e6edf3','fg_subtle':'#c9d1d9','fg_more_subtle':'#8b949e','fg_most_subtle':'#6e7681','button_subtle':'#182236',
        'info':'#00e5ff','info_more_subtle':'#00838f','info_most_subtle':'#004d40',
        'success':'#00e676','success_more_subtle':'#1b5e20','success_most_subtle':'#0d2b14',
        'warning':'#ffab00','warning_subtle':'#8f6000','error':'#ff5252','plan':'#00e5ff','plan_more_subtle':'#003344',
        'diff_insert_fg':'#69f0ae','diff_insert_code_bg':'#0d2b1d','diff_insert_gutter_bg':'#143d29',
        'diff_delete_fg':'#ff8a80','diff_delete_code_bg':'#2e1419','diff_delete_gutter_bg':'#401b22',
        'ansi_black':'#484f58','ansi_red':'#ff5252','ansi_green':'#00e676','ansi_yellow':'#ffd740',
        'ansi_blue':'#40c4ff','ansi_magenta':'#e040fb','ansi_cyan':'#00e5ff','ansi_white':'#c9d1d9',
        'ansi_bright_black':'#6e7681','ansi_bright_red':'#ff8a80','ansi_bright_green':'#69f0ae','ansi_bright_yellow':'#ffe57f',
        'ansi_bright_blue':'#80d8ff','ansi_bright_magenta':'#ea80fc','ansi_bright_cyan':'#84ffff','ansi_bright_white':'#ffffff'
    },
    'harmonika-mocha': {
        'base':'charmtone-panther','primary':'#fab387','secondary':'#cba6f7','accent':'#f9e2af','on_primary':'#181825',
        'bg_base':'#181825','bg_less_visible':'#1e1e2e','bg_least_visible':'#313244','bg_most_visible':'#45475a',
        'fg_base':'#cdd6f4','fg_subtle':'#bac2de','fg_more_subtle':'#a6adc8','fg_most_subtle':'#6c7086','button_subtle':'#313244',
        'info':'#89b4fa','info_more_subtle':'#4569a0','info_most_subtle':'#253759',
        'success':'#a6e3a1','success_more_subtle':'#3a6e43','success_most_subtle':'#1f3a25',
        'warning':'#f9e2af','warning_subtle':'#8c7848','error':'#f38ba8','plan':'#fab387','plan_more_subtle':'#4a3222',
        'diff_insert_fg':'#a6e3a1','diff_insert_code_bg':'#1e3326','diff_insert_gutter_bg':'#274231',
        'diff_delete_fg':'#f38ba8','diff_delete_code_bg':'#361f28','diff_delete_gutter_bg':'#472834',
        'ansi_black':'#45475a','ansi_red':'#f38ba8','ansi_green':'#a6e3a1','ansi_yellow':'#f9e2af',
        'ansi_blue':'#89b4fa','ansi_magenta':'#cba6f7','ansi_cyan':'#94e2d5','ansi_white':'#bac2de',
        'ansi_bright_black':'#585b70','ansi_bright_red':'#f38ba8','ansi_bright_green':'#a6e3a1','ansi_bright_yellow':'#f9e2af',
        'ansi_bright_blue':'#89b4fa','ansi_bright_magenta':'#f5c2e7','ansi_bright_cyan':'#94e2d5','ansi_bright_white':'#ffffff'
    },
}

def ensure_themes(cfg_dir=None,data_dir=None):
    cdir=Path(cfg_dir) if cfg_dir else (Path.home()/'.config/harmonika')
    ddir=Path(data_dir) if data_dir else (Path.home()/'.local/share/harmonika')
    themes_dir=cdir/'themes'
    themes_dir.mkdir(parents=True,exist_ok=True)
    for tname,tval in HARMONIKA_THEMES.items():
        (themes_dir/(tname+'.json')).write_text(json.dumps(tval,indent=2)+'\n')
    ddir.mkdir(parents=True,exist_ok=True)
    return themes_dir

def switch_theme(args,cfg_dir=None,data_dir=None):
    cdir=Path(cfg_dir) if cfg_dir else (Path.home()/'.config/harmonika')
    ddir=Path(data_dir) if data_dir else (Path.home()/'.local/share/harmonika')
    ensure_themes(cdir,ddir)
    aliases={'dark':'harmonika-dark','harmonika-dark':'harmonika-dark','orange':'harmonika-dark',
             'cyber':'harmonika-cyber','harmonika-cyber':'harmonika-cyber','neon':'harmonika-cyber',
             'mocha':'harmonika-mocha','harmonika-mocha':'harmonika-mocha','warm':'harmonika-mocha'}
    h_cfg_path=cdir/'harmonika.json'
    c_cfg_path=cdir/'crush.json'
    d_cfg_path=ddir/'crush.json'
    cur_theme='harmonika-dark'
    if h_cfg_path.is_file():
        try:cur_theme=json.loads(h_cfg_path.read_text()).get('options',{}).get('tui',{}).get('active_theme','harmonika-dark')
        except Exception:pass
    O='\033[38;2;255;123;0m';B='\033[1m';G='\033[38;2;63;185;80m';C='\033[38;2;0;229;255m';M='\033[38;2;250;179;135m';R='\033[0m'
    if not args:
        print(f"\n{O}╭──────────────────────────────────────────────────────────────────────────╮{R}")
        print(f"{O}│{R}  {B}{O}HARMONIKA AI — PILIHAN TEMA TERMINAL MODERN{R}                             {O}│{R}")
        print(f"{O}├──────────────────────────────────────────────────────────────────────────┤{R}")
        print(f"{O}│{R}  Tema Aktif Saat Ini : {G}{B}{cur_theme:<49}{R} {O}│{R}")
        print(f"{O}│{R}                                                                          {O}│{R}")
        print(f"{O}│{R}  Pilihan Tema Resmi:                                                     {O}│{R}")
        print(f"{O}│{R}    • {O}{B}harmonika-dark {R} — Signature Orange & Deep Obsidian (Default)        {O}│{R}")
        print(f"{O}│{R}    • {C}{B}harmonika-cyber{R} — Modern Cyber Neon (Electric Cyan & Midnight)      {O}│{R}")
        print(f"{O}│{R}    • {M}{B}harmonika-mocha{R} — Eye-Comfort Warm Mocha (Nyaman baca lama)         {O}│{R}")
        print(f"{O}│{R}                                                                          {O}│{R}")
        print(f"{O}│{R}  Cara ganti: {B}harmonika theme dark|cyber|mocha{R} atau tekan {B}Ctrl+P{R} di TUI  {O}│{R}")
        print(f"{O}╰──────────────────────────────────────────────────────────────────────────╯{R}\n")
        return {'ok':True,'active_theme':cur_theme,'available_themes':list(HARMONIKA_THEMES)}
    chosen=aliases.get(args[0].strip().lower())
    if not chosen:
        raise ValueError('invalid_theme_use_dark_cyber_or_mocha')
    for p in (h_cfg_path,c_cfg_path):
        if p.is_file():
            try:
                obj=json.loads(p.read_text())
                tui=obj.setdefault('options',{}).setdefault('tui',{})
                tui['active_theme']=chosen
                tui['compact_mode']=False
                p.write_text(json.dumps(obj,indent=2)+'\n')
            except Exception:pass
    try:
        d_obj=json.loads(d_cfg_path.read_text()) if d_cfg_path.is_file() else {}
        d_tui=d_obj.setdefault('options',{}).setdefault('tui',{})
        d_tui['active_theme']=chosen
        d_tui['transparent']=False
        d_tui['compact_mode']=False
        d_cfg_path.write_text(json.dumps(d_obj,indent=2)+'\n')
    except Exception:pass
    print(f"{G}✓ Tema Harmonika TUI berhasil diubah ke:{R} {B}{chosen}{R}")
    return {'ok':True,'active_theme':chosen}

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
            "# Aturan Penyimpanan File Otomatis & Pengalaman Visual Harmonika AI\n\n"
            "1. **Sesuai Permintaan User (Custom Folder):**\n"
            "   Jika pengguna meminta secara spesifik untuk membuat folder baru (di `Downloads` atau `Projects`) atau menyimpan di folder proyek tertentu, ikuti permintaan pengguna dan gunakan tool `create_directory` bila membuat folder proyek baru.\n\n"
            "2. **Inisiatif Penyimpanan Otomatis Berdasarkan Jenis File (`Downloads/harmonika-ai`):**\n"
            "   Jika pengguna meminta membuat/mengunduh/menyimpan file tanpa menentukan folder tujuan spesifik, atau meminta disimpan di Downloads, simpan secara otomatis dan terfokus di dalam folder khusus berikut yang sudah siap pakai (auto-created):\n"
            f"   - **Folder Utama Harmonika AI:** `{dl_abs}`\n"
            f"   - **File Gambar** (`.png`, `.jpg`, `.jpeg`, `.webp`, `.svg`, `.gif` — hasil generate gambar, desain, poster, screenshot): simpan di `{gambar_abs}`\n"
            f"   - **File Dokumen** (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.csv`, `.txt`, `.md`, `.html` — hasil dokumen, laporan, tabel, presentasi): simpan di `{doc_abs}`\n"
            f"   - **File Arsip / Bundle** (`.zip`, `.tar.gz`, `.tgz`, `.7z` — hasil kompresi atau paket kode/file): simpan di `{zip_abs}` (bisa juga menjalankan `harmonika zip <path_folder> [nama.zip]`)\n\n"
            "3. **Gaya Respons, Coding & Pembuatan Gambar Modern di Terminal:**\n"
            "   - Untuk tugas coding atau analisis multi-langkah, sajikan tahapan kerja yang rapi (contoh: `1. ✓ Parse permintaan` • `2. ✓ Analisis kode` • `3. ✓ Implementasi solusi`) serta gunakan indikator status jelas (`✓ Sukses`, `⚠ Catatan`, `✗ Error`, `ℹ Info`).\n"
            "   - Gunakan format Markdown yang terstruktur (bold, list, tabel ringkasan, dan blok kode dengan nama bahasa untuk syntax highlighting).\n"
            f"   - Saat pengguna meminta membuat gambar (`generate_image`), gunakan path output di dalam `{gambar_abs}/<nama-unik>.png` (kecuali diminta di folder lain).\n"
            "   - Sajikan informasi hasil pembuatan gambar dengan rapi (Nama File, Dimensi, Ukuran File, Lokasi Penyimpanan, dan informasikan bahwa pengguna dapat mengetik `harmonika preview` di terminal untuk melihat pratinjau warna gambar langsung di terminal).\n"
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
        notes=newest.get('release_notes') if isinstance(newest.get('release_notes'),list) else []
        O='\033[38;2;255;123;0m';B='\033[1m';G='\033[38;2;63;185;80m';C='\033[38;2;88;166;255m';R='\033[0m'
        print(f"\n{O}╭──────────────────────────────────────────────────────────────────────────╮{R}")
        print(f"{O}│{R}  {B}{O}HARMONIKA AI — PEMBARUAN TERBARU TERSEDIA{R}                               {O}│{R}")
        print(f"{O}│{R}                                                                          {O}│{R}")
        print(f"{O}│{R}  Versi Saat Ini : {C}{cur_ver:<54}{R} {O}│{R}")
        print(f"{O}│{R}  Versi Terbaru  : {G}{B}{release:<54}{R} {O}│{R}")
        if notes:
            print(f"{O}│{R}                                                                          {O}│{R}")
            print(f"{O}│{R}  {B}Apa yang baru di versi ini:{R}                                             {O}│{R}")
            for note in notes[:4]:
                clean_note=str(note).strip()[:66]
                print(f"{O}│{R}    • {clean_note:<67} {O}│{R}")
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
                # If platform binary was also updated in signed binaries.json, download & verify it before applying package
                cur_dir,cur_manifest=active(root)
                with zipfile.ZipFile(tmp_pkg) as zchk:
                    pkg_manifest=verify_envelope(zchk.read('release.json'),root)
                if expected_binary_sha(pkg_manifest)!=expected_binary_sha(cur_manifest):
                    bins_url='https://chat.harmonika.id/assets/harmonika-terminal/binaries.json'
                    with urllib.request.build_opener(SameOrigin()).open(bins_url,timeout=30) as br:
                        bins_env=verify_envelope(br.read(1024*1024),root)
                    pinfo=bins_env.get('platforms',{}).get(platform_id())
                    if not pinfo or pinfo.get('sha256')!=expected_binary_sha(pkg_manifest):
                        raise ValueError('platform_catalog_mismatch')
                    tmp_bin=root/('update-bin-'+bin_name())
                    try:
                        with urllib.request.build_opener(SameOrigin()).open(pinfo['url'],timeout=300) as br2, open(tmp_bin,'wb') as bout:
                            shutil.copyfileobj(br2,bout)
                        if digest(tmp_bin)!=pinfo['sha256']:
                            raise ValueError('platform_binary_checksum_mismatch')
                        os.chmod(tmp_bin,0o755)
                        # Stage and verify the new binary; never overwrite the
                        # active or rollback release while updating metadata.
                        install_package(tmp_pkg,root,binary_source=tmp_bin)
                    finally:
                        tmp_bin.unlink(missing_ok=True)
                else:
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

def _decode_png_rgb(raw_bytes):
    import struct as _st, zlib as _zl
    if raw_bytes[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('not_a_png')
    pos=8;w=h=bit_depth=color_type=0;idat=bytearray()
    while pos<len(raw_bytes):
        length=_st.unpack('>I',raw_bytes[pos:pos+4])[0]
        ctype=raw_bytes[pos+4:pos+8]
        chunk=raw_bytes[pos+8:pos+8+length]
        pos+=12+length
        if ctype==b'IHDR':
            w,h,bit_depth,color_type,_,_,_=_st.unpack('>IIBBBBB',chunk)
        elif ctype==b'IDAT':
            idat.extend(chunk)
        elif ctype==b'IEND':
            break
    if bit_depth!=8 or color_type not in (2,6):raise ValueError('unsupported_png_format')
    bpp=3 if color_type==2 else 4
    stride=w*bpp
    data=_zl.decompress(bytes(idat))
    rows=[];prev=bytearray(stride);idx=0
    for _ in range(h):
        ftype=data[idx];idx+=1
        row=bytearray(data[idx:idx+stride]);idx+=stride
        if ftype==1:
            for i in range(bpp,stride):row[i]=(row[i]+row[i-bpp])&0xff
        elif ftype==2:
            for i in range(stride):row[i]=(row[i]+prev[i])&0xff
        elif ftype==3:
            for i in range(stride):
                a=row[i-bpp] if i>=bpp else 0
                row[i]=(row[i]+((a+prev[i])>>1))&0xff
        elif ftype==4:
            for i in range(stride):
                a=row[i-bpp] if i>=bpp else 0
                b=prev[i]
                c=prev[i-bpp] if i>=bpp else 0
                p=a+b-c;pa=abs(p-a);pb=abs(p-b);pc=abs(p-c)
                pr=a if (pa<=pb and pa<=pc) else (b if pb<=pc else c)
                row[i]=(row[i]+pr)&0xff
        rows.append(row);prev=row
    return w,h,bpp,rows

def preview_image(args,cfg_dir=None):
    dl_root=ensure_harmonika_downloads(cfg_dir)
    gambar_dir=dl_root/'gambar'
    target=None
    if args:
        cand=Path(args[0]).expanduser()
        if not cand.is_absolute() and not cand.exists() and (gambar_dir/cand.name).is_file():
            cand=gambar_dir/cand.name
        target=cand.resolve()
    else:
        imgs=sorted([p for p in gambar_dir.glob('*.png') if p.is_file()],key=lambda x:x.stat().st_mtime,reverse=True)
        if not imgs:raise ValueError('no_images_in_harmonika_gambar_folder')
        target=imgs[0]
    if not target.is_file():raise ValueError('image_file_not_found')
    raw=target.read_bytes()
    w,h,bpp,rows=_decode_png_rgb(raw)
    cols=min(56,max(24,w))
    rows_out=max(12,int((h/max(1,w))*cols*0.95))
    if rows_out%2==1:rows_out+=1
    O='\033[38;2;255;123;0m';B='\033[1m';G='\033[38;2;63;185;80m';C='\033[38;2;88;166;255m';R='\033[0m'
    print(f"\n{O}╭──────────────────────────────────────────────────────────────────────────╮{R}")
    print(f"{O}│{R}  {B}{O}HARMONIKA AI — TERMINAL TRUECOLOR IMAGE PREVIEW{R}                         {O}│{R}")
    print(f"{O}├──────────────────────────────────────────────────────────────────────────┤{R}")
    print(f"{O}│{R}  File     : {B}{target.name}{R}")
    print(f"{O}│{R}  Resolusi : {G}{w}x{h} px{R} • Ukuran: {C}{len(raw)/1024:.1f} KB{R}")
    print(f"{O}│{R}  Lokasi   : {target}")
    print(f"{O}╰──────────────────────────────────────────────────────────────────────────╯{R}")
    for y in range(0,rows_out,2):
        sy1=min(h-1,int(y*h/rows_out))
        sy2=min(h-1,int((y+1)*h/rows_out))
        r1=rows[sy1];r2=rows[sy2]
        line=['  ']
        for x in range(cols):
            sx=min(w-1,int(x*w/cols))
            o1=sx*bpp;o2=sx*bpp
            tr,tg,tb=r1[o1],r1[o1+1],r1[o1+2]
            br,bg,bb=r2[o2],r2[o2+1],r2[o2+2]
            line.append(f"\033[38;2;{tr};{tg};{tb}m\033[48;2;{br};{bg};{bb}m▀")
        line.append(f"{R}")
        print(''.join(line))
    print()
    return {'ok':True,'file':str(target),'width':w,'height':h,'size_bytes':len(raw)}

def create_zip_bundle(args,cfg_dir=None):
    dl_root=ensure_harmonika_downloads(cfg_dir)
    zip_dir=dl_root/'zip'
    zip_dir.mkdir(parents=True,exist_ok=True)
    src_path=Path(args[0]).expanduser().resolve() if args else Path.cwd().resolve()
    if not src_path.exists():
        raise ValueError('zip_source_not_found')
    from datetime import datetime
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    base_name=args[1] if len(args)>1 else f"{src_path.stem or 'project'}-{stamp}.zip"
    if not base_name.lower().endswith('.zip'):
        base_name+='.zip'
    out_zip=Path(base_name).expanduser().resolve() if Path(base_name).is_absolute() else (zip_dir/Path(base_name).name)
    out_zip.parent.mkdir(parents=True,exist_ok=True)
    skip_dirs={'node_modules','.git','__pycache__','.venv','venv','.harmonika','.pytest_cache','.mypy_cache','.next','dist_cache'}
    skip_files={'.DS_Store','Thumbs.db','.env','harmonika-api-key'}
    count=0
    with zipfile.ZipFile(out_zip,'w',zipfile.ZIP_DEFLATED) as zf:
        if src_path.is_file():
            zf.write(src_path,src_path.name)
            count=1
        else:
            for item in sorted(src_path.rglob('*')):
                if item.is_symlink():
                    continue
                rel_parts=item.relative_to(src_path).parts
                if any(p in skip_dirs or (p.startswith('.') and p not in {'.github','.env.example'}) for p in rel_parts[:-1]):
                    continue
                if item.is_file():
                    if item.name in skip_files or item.name.endswith(('.pem','.key','.pfx','.p12')):
                        continue
                    if item.resolve()==out_zip.resolve():
                        continue
                    zf.write(item,str(item.relative_to(src_path)))
                    count+=1
    size_bytes=out_zip.stat().st_size
    O='\033[38;2;255;123;0m';G='\033[38;2;63;185;80m';B='\033[1m';R='\033[0m'
    print(f"{G}✓ Paket ZIP berhasil dibuat:{R} {B}{out_zip}{R} ({count} file, {size_bytes/1024:.1f} KB)")
    return {'ok':True,'zip_file':str(out_zip),'source':str(src_path),'files_count':count,'size_bytes':size_bytes}

def show_status(root=ROOT,as_json=False):
    directory,manifest=active(root)
    cfg_dir=Path(os.environ.get('HARMONIKA_GLOBAL_CONFIG',str(Path.home()/'.config/harmonika')))
    dl_root=ensure_harmonika_downloads(cfg_dir)
    dev_id=device_id(cfg_dir)
    dev_nm=device_name()
    key_file=cfg_dir/'harmonika-api-key'
    has_key=key_file.is_file()
    lic_info={'valid':False,'status':'not_activated'}
    if has_key:
        try:
            lic_mod_path=directory/'tools/license.py'
            if not lic_mod_path.exists():lic_mod_path=Path(__file__).resolve().parent/'license.py'
            spec=importlib.util.spec_from_file_location('harmonika_lic',lic_mod_path)
            lic_mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(lic_mod)
            lic_info=lic_mod.check(key_file.read_text().strip())
        except Exception as e:
            lic_info={'valid':False,'status':str(e)}
    folder_stats={}
    for sub in ('gambar','document','zip'):
        sp=dl_root/sub
        files=[p for p in sp.iterdir() if p.is_file() and not p.name.startswith('.')] if sp.is_dir() else []
        folder_stats[sub]={'path':str(sp),'count':len(files)}
    skills_count=len(list((directory/'skills').glob('*/SKILL.md')))
    status_data={
        'ok':True,
        'product':'Harmonika AI Terminal',
        'release':manifest['release'],
        'upstream_version':manifest.get('upstream_version','0.98.1'),
        'device':{'name':dev_nm,'id':dev_id,'policy':'1 Key = 1 Device'},
        'license':lic_info,
        'skills_installed':skills_count,
        'downloads_workspace':{'root':str(dl_root),'folders':folder_stats},
    }
    if as_json:
        return status_data
    O='\033[38;2;255;123;0m';B='\033[1m';G='\033[38;2;63;185;80m';C='\033[38;2;88;166;255m';Y='\033[38;2;255;158;0m';R='\033[0m'
    lic_badge=f"{G}{B}AKTIF (Verified){R}" if lic_info.get('valid') else f"{Y}{B}BELUM AKTIF / PERLU CEK ({lic_info.get('status')}){R}"
    quota_str='Unlimited' if lic_info.get('unlimited') else (f"{lic_info.get('requests_per_day')} req/hari" if lic_info.get('requests_per_day') else '-')
    print(f"\n{O}╭──────────────────────────────────────────────────────────────────────────╮{R}")
    print(f"{O}│{R}  {B}{O}HARMONIKA AI — STATUS TERMINAL & LISENSI{R}                                {O}│{R}")
    print(f"{O}├──────────────────────────────────────────────────────────────────────────┤{R}")
    print(f"{O}│{R}  Versi Rilis    : {C}{manifest['release']}{R} (Engine v0.98.1 • {skills_count} Skills Aktif)")
    print(f"{O}│{R}  Perangkat      : {B}{dev_nm}{R}")
    print(f"{O}│{R}  Device ID      : {C}{dev_id}{R} (1 Key = 1 Device)")
    print(f"{O}│{R}  Status Lisensi : {lic_badge} • Kuota: {B}{quota_str}{R}")
    print(f"{O}├──────────────────────────────────────────────────────────────────────────┤{R}")
    print(f"{O}│{R}  {B}Folder Auto-Save Harmonika AI ({dl_root}):{R}")
    print(f"{O}│{R}    • {B}gambar/  {R} : {G}{folder_stats['gambar']['count']} file{R}  ({folder_stats['gambar']['path']})")
    print(f"{O}│{R}    • {B}document/{R} : {G}{folder_stats['document']['count']} file{R}  ({folder_stats['document']['path']})")
    print(f"{O}│{R}    • {B}zip/     {R} : {G}{folder_stats['zip']['count']} file{R}  ({folder_stats['zip']['path']})")
    print(f"{O}╰──────────────────────────────────────────────────────────────────────────╯{R}\n")
    return status_data

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
    elif args in (['status'],['status','--json']):
        res=show_status(ROOT,as_json=('--json' in args))
        if '--json' not in args:return
        result=res
    elif args and args[0]=='theme':
        res=switch_theme(args[1:],os.environ.get('HARMONIKA_GLOBAL_CONFIG',str(Path.home()/'.config/harmonika')),os.environ.get('HARMONIKA_GLOBAL_DATA',str(Path.home()/'.local/share/harmonika')))
        if not args[1:]:return
        result=res
    elif args and args[0]=='preview':
        res=preview_image(args[1:],os.environ.get('HARMONIKA_GLOBAL_CONFIG',str(Path.home()/'.config/harmonika')))
        return
    elif args and args[0] in ('zip','export'):
        result=create_zip_bundle(args[1:],os.environ.get('HARMONIKA_GLOBAL_CONFIG',str(Path.home()/'.config/harmonika')))
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
        # Ensure crush.json, harmonika.json, data_dir/crush.json, and all 3 modern themes stay in sync
        try:
            ensure_themes(cfg_dir,data_dir)
            h_cfg_path=Path(cfg_dir)/'harmonika.json'
            c_cfg_path=Path(cfg_dir)/'crush.json'
            data_crush=Path(data_dir)/'crush.json'
            active_theme='harmonika-dark'
            if data_crush.is_file():
                try:
                    d_obj=json.loads(data_crush.read_text())
                    t_cand=d_obj.get('options',{}).get('tui',{}).get('active_theme','')
                    if t_cand in HARMONIKA_THEMES:active_theme=t_cand
                except Exception:pass
            if h_cfg_path.is_file():
                cfg_obj=json.loads(h_cfg_path.read_text())
                t_cand2=cfg_obj.get('options',{}).get('tui',{}).get('active_theme','')
                if active_theme=='harmonika-dark' and t_cand2 in HARMONIKA_THEMES:
                    active_theme=t_cand2
                prov=cfg_obj.setdefault('providers',{}).setdefault('harmonika',{})
                prov['base_url']='https://chat.harmonika.id/v1'
                if api_key_val and os.name=='nt':
                    prov['api_key']=api_key_val
                extra_hdrs=prov.setdefault('extra_headers',{})
                extra_hdrs['X-Harmonika-Device-ID']=dev_id
                extra_hdrs['X-Harmonika-Device-Name']=dev_nm
                tui_opts=cfg_obj.setdefault('options',{}).setdefault('tui',{})
                tui_opts['active_theme']=active_theme
                tui_opts['compact_mode']=False
                tui_opts['scrollbar']='always'
                rendered_cfg=json.dumps(cfg_obj,indent=2)+'\n'
                h_cfg_path.write_text(rendered_cfg)
                c_cfg_path.write_text(rendered_cfg)
            if not data_crush.is_file():
                data_crush.write_text(json.dumps({'options':{'tui':{'transparent':False,'compact_mode':False,'active_theme':active_theme}}})+'\n')
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
