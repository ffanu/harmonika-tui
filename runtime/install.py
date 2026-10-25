#!/usr/bin/python3
"""Bootstrap Harmonika TUI across macOS, Linux, and Windows with Ed25519 & SHA-256 verification."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import stat
import urllib.request
from uuid import uuid4
import zipfile

SOURCE=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('launcher',SOURCE/'runtime/harmonika.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ROOT=m.ROOT
BINARY=Path(os.environ.get('HARMONIKA_BINARY_SOURCE',str(ROOT/'current'/m.bin_name())))
ASSETS_BASE='https://chat.harmonika.id/assets/harmonika-terminal'
PUBLIC_KEY_SHA256='0d07aa02cdde89cb447713c6879747ba0d80bd2b69b7a12d8c618f325a62bea5'

def detect_platform():
    sys_name=platform.system().lower()
    arch=platform.machine().lower()
    if arch in ('x86_64','amd64'):arch='x64'
    elif arch in ('aarch64','arm64'):arch='arm64'
    if sys_name=='darwin' and arch in ('arm64','x64'):return 'darwin-'+arch
    if sys_name=='linux' and arch in ('x64','arm64'):return 'linux-'+arch
    if sys_name=='windows' and arch=='x64':return 'windows-x64'
    raise ValueError('unsupported_platform:'+sys_name+'-'+arch)

def ensure_public_key(root=ROOT):
    root.mkdir(parents=True,exist_ok=True)
    pub=root/'trusted-public.pem'
    if not pub.is_file():
        with urllib.request.build_opener(m.SameOrigin()).open(ASSETS_BASE+'/trusted-public.pem',timeout=20) as r:
            pub.write_bytes(r.read(4096))
    if m.digest(pub)!=PUBLIC_KEY_SHA256:raise ValueError('untrusted_public_key')
    return pub

def ensure_dependencies(root=ROOT):
    tools_dir=Path(os.environ.get('HARMONIKA_TOOLS_HOME',str(Path.home()/'.config/harmonika/harmonika-tools')))
    if not (tools_dir/'node_modules/@modelcontextprotocol/sdk').exists() or not (tools_dir/'node_modules/ajv').exists():
        npm=shutil.which('npm') or str(Path.home()/'.local/node/bin/npm')
        if Path(npm).exists() or shutil.which('npm'):
            tools_dir.mkdir(parents=True,exist_ok=True)
            pkg={'name':'harmonika-tools','private':True,'dependencies':{'@modelcontextprotocol/sdk':'^1.0.0','ajv':'^8.17.1'}}
            (tools_dir/'package.json').write_text(json.dumps(pkg,indent=2))
            subprocess.run([npm,'install','--no-audit','--no-fund'],cwd=str(tools_dir),check=True,timeout=180)

def ensure_default_config(root=ROOT):
    cfg_dir=Path.home()/'.config/harmonika'
    cfg_dir.mkdir(parents=True,exist_ok=True)
    themes_dir=cfg_dir/'themes'
    themes_dir.mkdir(parents=True,exist_ok=True)
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
    (themes_dir/'harmonika-dark.json').write_text(json.dumps(orange_dark_theme,indent=2)+'\n')
    share_dir=Path.home()/'.local/share/harmonika'
    share_dir.mkdir(parents=True,exist_ok=True)
    (share_dir/'crush.json').write_text('{"options":{"tui":{"transparent":false,"active_theme":"harmonika-dark"}}}\n')
    cfg_file=cfg_dir/'harmonika.json'
    key_file=cfg_dir/'harmonika-api-key'
    key_cmd=f'$(cat {key_file})' if os.name!='nt' else '$HARMONIKA_API_KEY'
    if not cfg_file.exists():
        default_cfg={
            'providers':{'harmonika':{'id':'harmonika','name':'Harmonika Ai','type':'openai-compat','base_url':'https://chat.harmonika.id/v1','api_key':key_cmd,'discover_models':False,'models':[{'id':'harmonika-ai','name':'Harmonika Ai','context_window':16000,'default_max_tokens':4096,'can_reason':False,'supports_attachments':False,'cost_per_1m_in':0,'cost_per_1m_out':0,'cost_per_1m_in_cached':0,'cost_per_1m_out_cached':0}]}},
            'models':{'large':{'provider':'harmonika','model':'harmonika-ai','max_tokens':4096},'small':{'provider':'harmonika','model':'harmonika-ai','max_tokens':4096}},
            'options':{
                'disabled_tools':['agent','crush_info','crush_logs','job_output','job_kill','download','lsp_restart','lsp_rename','lsp_replace_symbol','fetch','agentic_fetch','sourcegraph','list_mcp_resources','read_mcp_resource'],
                'progress':True,'auto_lsp':False,'data_directory':'.harmonika','disable_provider_auto_update':True,
                'disabled_skills':['built-in-browser','chrome-browser','computer-use','crush-config','crush-hooks','harmonika-config','harmonika-hooks','deep-research','docs','docx','google-workspace','import-memory','jq','learn','morning','pdf','pptx','setup-writing-style','skill-creator','theme-factory','xlsx'],
                'request_timeout':120,
                'tui':{'compact_mode':True,'diff_mode':'unified','active_theme':'harmonika-dark','scrollbar':'always','mouse':True,'exit_banner':'compact'},
                'skills_paths':[str(root/'current/skills')]
            },
            'permissions':{'allowed_tools':[]},
            'hooks':{'PreToolUse':[{'name':'Harmonika project file guard','matcher':'^(view|ls|glob|grep|write|edit|multiedit|bash|lsp_diagnostics|lsp_references|lsp_symbols|lsp_definition|lsp_implementation|lsp_call_hierarchy)$','command':f'{m.NODE} {root}/current/tools/project-file-guard.cjs','timeout':5}]},
            'mcp':{'harmonika':{'type':'stdio','command':m.NODE,'args':[str(root/'current/tools/mcp-server.cjs')],'timeout':240}}
        }
        cfg_file.write_text(json.dumps(default_cfg,indent=2)+'\n')
        crush_alias=cfg_dir/'crush.json'
        if not crush_alias.exists():shutil.copy2(cfg_file,crush_alias)

def fetch_remote_bootstrap(root=ROOT):
    ensure_public_key(root)
    plat=detect_platform()
    with urllib.request.build_opener(m.SameOrigin()).open(ASSETS_BASE+'/binaries.json',timeout=30) as r:
        bins_manifest=m.verify_envelope(r.read(1024*1024),root)
    plat_info=bins_manifest.get('platforms',{}).get(plat)
    if not plat_info:raise ValueError('platform_binary_unavailable:'+plat)
    bin_dest=root/('bootstrap-'+m.bin_name())
    with urllib.request.build_opener(m.SameOrigin()).open(plat_info['url'],timeout=300) as r, open(bin_dest,'wb') as out:
        shutil.copyfileobj(r,out)
    if m.digest(bin_dest)!=plat_info['sha256']:
        bin_dest.unlink(missing_ok=True);raise ValueError('platform_binary_checksum_mismatch')
    os.chmod(bin_dest,0o755)
    with urllib.request.build_opener(m.SameOrigin()).open(ASSETS_BASE+'/stable.json',timeout=30) as r:
        stable=m.verify_envelope(r.read(1024*1024),root)
    pkg_dest=root/('bootstrap-'+stable['release']+'.zip')
    with urllib.request.build_opener(m.SameOrigin()).open(stable['package_url'],timeout=120) as r, open(pkg_dest,'wb') as out:
        shutil.copyfileobj(r,out)
    if m.digest(pkg_dest)!=stable['package_sha256']:
        pkg_dest.unlink(missing_ok=True);raise ValueError('package_checksum_mismatch')
    return bin_dest,pkg_dest

def atomic_text(file,text,mode=0o600):
    pending=file.with_name(file.name+'.next-'+uuid4().hex)
    try:
        with open(pending,'x') as out:out.write(text)
        os.chmod(pending,mode);os.replace(pending,file)
    finally:
        if pending.exists():pending.unlink()

def prepare(package):
    if Path(package).stat().st_size>20*1024*1024:raise ValueError('package_too_large')
    (ROOT/'versions').mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.bootstrap-',dir=str(ROOT/'versions')))
    try:
        _prepare(package,stage)
        manifest=m.verify_envelope((stage/'release.json').read_bytes(),ROOT)
        return stage,manifest
    except Exception:
        shutil.rmtree(stage)
        raise

def _prepare(package,stage):
    with zipfile.ZipFile(package) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or len(names)>512 or sum(i.file_size for i in archive.infolist())>20*1024*1024:raise ValueError('invalid_package')
        for info in archive.infolist():
            if Path(info.filename).is_absolute() or '..' in Path(info.filename).parts or '\\' in info.filename or stat.S_ISLNK(info.external_attr>>16):raise ValueError('unsafe_path')
        manifest=m.verify_envelope(archive.read('release.json'),ROOT)
        release=manifest['release']
        if not m.RELEASE_RE.fullmatch(release):raise ValueError('invalid_release')
        if set(archive.namelist())!=set(manifest['files'])|{'release.json'}:raise ValueError('package_mismatch')
        for name in [*manifest['files'],'release.json']:
            if name!='release.json' and not (name.startswith('tools/') or name.startswith('skills/') or name=='README.md'):raise ValueError('unsupported_runtime_file')
            file=stage/name;file.parent.mkdir(parents=True,exist_ok=True)
            file.write_bytes(archive.read(name))
        if os.environ.get('HARMONIKA_SKIP_BINARY_SHA_CHECK')!='1' and m.digest(BINARY)!=manifest['binary_sha256']:raise ValueError('custom_binary_changed')
        bname=m.bin_name() if BINARY.name.endswith('.exe') else 'harmonika'
        shutil.copy2(BINARY,stage/bname);os.chmod(stage/bname,0o755)
        m.preflight(stage,manifest)

def main(package=None):
    global BINARY
    if (ROOT/'current').exists():raise ValueError('already_installed_use_manual_update')
    downloaded_bin=downloaded_pkg=None
    try:
        if not package:
            ensure_dependencies(ROOT)
            ensure_default_config(ROOT)
            downloaded_bin,downloaded_pkg=fetch_remote_bootstrap(ROOT)
            BINARY=downloaded_bin
            package=str(downloaded_pkg)
            if detect_platform()!='darwin-arm64':
                os.environ['HARMONIKA_SKIP_BINARY_SHA_CHECK']='1'
        stage,manifest=prepare(package)
        try:activate(stage,manifest)
        finally:
            if stage.exists():shutil.rmtree(stage)
    finally:
        if downloaded_bin and downloaded_bin.exists():downloaded_bin.unlink(missing_ok=True)
        if downloaded_pkg and downloaded_pkg.exists():downloaded_pkg.unlink(missing_ok=True)

def activate(stage,manifest):
    release=manifest['release']
    configfile=Path.home()/'.config/harmonika/harmonika.json'
    if configfile.exists():configfile=configfile.resolve()
    config=json.loads(configfile.read_text())
    config['options']['disable_provider_auto_update']=True
    config['options']['data_directory']='.harmonika'
    incompatible=['built-in-browser','chrome-browser','computer-use','crush-config','crush-hooks','harmonika-config','harmonika-hooks','deep-research','docs','docx','google-workspace','import-memory','jq','learn','morning','pdf','pptx','setup-writing-style','skill-creator','theme-factory','xlsx']
    disabled=config['options'].setdefault('disabled_skills',[])
    for name in incompatible:
        if name not in disabled:disabled.append(name)
    paths=config['options'].setdefault('skills_paths',[])
    skillpath=str(ROOT/'current/skills')
    if skillpath not in paths:paths.append(skillpath)
    config['mcp']['harmonika']['args']=[str(ROOT/'current/tools/mcp-server.cjs')]
    for hook in config.get('hooks',{}).get('PreToolUse',[]):
        if 'project-file-guard.cjs' in hook.get('command',''):
            hook['command']=m.NODE+' '+str(ROOT/'current/tools/project-file-guard.cjs')
    # Validate proposed configuration on stdin before touching live files/pointers.
    schema=SOURCE/'runtime/harmonika-schema.json'
    js="const fs=require('fs'),{Ajv2020}=require(process.argv[2]);const v=new Ajv2020({strict:false,validateFormats:false}).compile(JSON.parse(fs.readFileSync(process.argv[1])));if(!v(JSON.parse(fs.readFileSync(0,'utf8'))))process.exit(1);"
    encoded=json.dumps(config,indent=2)+'\n'
    dependency=Path(os.environ.get('HARMONIKA_TOOLS_HOME',str(Path.home()/'.config/harmonika/harmonika-tools')))/'node_modules/ajv/dist/2020'
    checked=subprocess.run([m.NODE,'-e',js,str(schema),str(dependency)],input=encoded.encode(),capture_output=True,timeout=15)
    if checked.returncode:
        raise ValueError('config_validation_failed_live_install_untouched')
    target=ROOT/'versions'/release
    if target.exists():
        if m.verify_envelope((target/'release.json').read_bytes(),ROOT)!=manifest:raise ValueError('release_name_conflict')
        m.preflight(target,manifest)
    else:stage.rename(target)
    if not (ROOT/'config-before.json').exists():
        shutil.copy2(configfile,ROOT/'config-before.json');os.chmod(ROOT/'config-before.json',0o600)
    atomic_text(ROOT/'launcher.py',(SOURCE/'runtime/harmonika.py').read_text())
    m.switch(release,ROOT)
    atomic_text(configfile,encoded)
    if os.name == 'nt':
        bin_dir = Path.home()/'.local/bin'
        bin_dir.mkdir(parents=True,exist_ok=True)
        cmd_wrapper = f'@echo off\r\n"{sys.executable}" -I "%USERPROFILE%\\.config\\harmonika-terminal\\launcher.py" %*\r\n'
        atomic_text(bin_dir/'harmonika.cmd',cmd_wrapper,0o755)
    else:
        wrapper=f'#!/bin/sh\nexec "{sys.executable}" -I "$HOME/.config/harmonika-terminal/launcher.py" "$@"\n'
        for file in [Path.home()/'.local/bin/harmonika',Path.home()/'.local/node/bin/harmonika']:
            file.parent.mkdir(parents=True,exist_ok=True)
            # Replace the symlink itself atomically, never the npm target or live binary.
            atomic_text(file,wrapper,0o755)
    print(json.dumps(m.doctor(),indent=2))

if __name__=='__main__':main(sys.argv[1] if len(sys.argv)>1 else None)
