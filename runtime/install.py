#!/usr/bin/python3
"""Bootstrap this owner's existing custom binary without modifying its npm files."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import stat
from uuid import uuid4
import zipfile

SOURCE=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('launcher',SOURCE/'runtime/harmonika.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ROOT=m.ROOT
BINARY=Path(os.environ.get('HARMONIKA_BINARY_SOURCE',str(Path.home()/'.local/node/lib/node_modules/@charmland/crush/bin/crush')))

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
        if m.digest(BINARY)!=manifest['binary_sha256']:raise ValueError('custom_binary_changed')
        shutil.copy2(BINARY,stage/'crush');os.chmod(stage/'crush',0o755)
        m.preflight(stage,manifest)

def main(package):
    if (ROOT/'current').exists():raise ValueError('already_installed_use_manual_update')
    stage,manifest=prepare(package)
    try:activate(stage,manifest)
    finally:
        if stage.exists():shutil.rmtree(stage)

def activate(stage,manifest):
    release=manifest['release']
    configfile=Path.home()/'.config/harmonika/harmonika.json'
    if configfile.exists():configfile=configfile.resolve()
    config=json.loads(configfile.read_text())
    config['options']['disable_provider_auto_update']=True
    config['options']['data_directory']='.harmonika'
    incompatible=['built-in-browser','chrome-browser','computer-use','crush-config','crush-hooks','deep-research','docs','docx','google-workspace','import-memory','jq','learn','morning','pdf','pptx','setup-writing-style','skill-creator','theme-factory','xlsx']
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
    schema=SOURCE/'runtime/crush-schema.json'
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
    wrapper='#!/bin/sh\nexec /usr/bin/python3 "$HOME/.config/harmonika-terminal/launcher.py" "$@"\n'
    for file in [Path.home()/'.local/bin/harmonika',Path.home()/'.local/node/bin/harmonika',Path.home()/'.local/bin/crush',Path.home()/'.local/node/bin/crush']:
        file.parent.mkdir(parents=True,exist_ok=True)
        # Replace the symlink itself atomically, never the npm target or live binary.
        atomic_text(file,wrapper,0o755)
    print(json.dumps(m.doctor(),indent=2))

if __name__=='__main__':main(sys.argv[1])
