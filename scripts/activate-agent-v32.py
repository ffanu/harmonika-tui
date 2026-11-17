#!/usr/bin/env python3
"""Atomically add an explicit agent-v32 entrypoint; preserve native UI and sessions."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import os
import shlex
import shutil
import subprocess

FILES = ('agent-v32.js', 'agent-state.cjs', 'agent-http.cjs')
MARKER = '# Harmonika verified agent v3.2 entrypoint\n'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def atomic(path, data, mode=0o600):
    tmp = path.with_name(path.name + '.v32-new')
    with tmp.open('xb') as handle:
        handle.write(data);handle.flush();os.fsync(handle.fileno())
    tmp.chmod(mode);os.replace(tmp, path)

def upgrade(root, source, node, expected, expected_files=None):
    root=root.resolve();base=root/'agent-v32';current=base/'current';launcher=root/'harmonika'
    before=launcher.read_bytes()
    if MARKER.encode() not in before or not current.is_symlink():raise RuntimeError('entrypoint_not_installed')
    previous=current.resolve()
    if previous.parent!=base or previous.name!=expected:raise RuntimeError('active_version_changed')
    actual={n:sha((previous/n).read_bytes()) for n in FILES}
    if expected_files is not None:
        if actual!=expected_files:raise RuntimeError('active_bundle_changed')
    elif sha(b''.join((previous/n).read_bytes() for n in FILES))[:16]!=expected:
        raise RuntimeError('active_bundle_changed')
    bundle={n:(source/n).read_bytes() for n in FILES};digest=sha(b''.join(bundle[n] for n in FILES))[:16]
    if digest==expected:raise RuntimeError('bundle_already_current')
    version=base/digest;version.mkdir(mode=0o700,exist_ok=True)
    for n,b in bundle.items():
        p=version/n
        if p.exists() and p.read_bytes()!=b:raise RuntimeError('version_collision')
        if not p.exists():p.write_bytes(b);p.chmod(0o600)
        subprocess.run([node,'--check',str(p)],check=True,capture_output=True)
    check=subprocess.run([node,str(version/'agent-v32.js'),'--help'],capture_output=True,text=True,timeout=10)
    if check.returncode or 'agent-v32' not in check.stdout:raise RuntimeError('candidate_canary_failed')
    if (current.resolve()!=previous or launcher.read_bytes()!=before or
            {n:sha((previous/n).read_bytes()) for n in FILES}!=actual):
        raise RuntimeError('activation_compare_failed')
    link=base/'current.v32-new';link.symlink_to(version);os.replace(link,current)
    try:
        check=subprocess.run([str(launcher),'agent-v32','--help'],capture_output=True,text=True,timeout=10)
        if check.returncode or 'agent-v32' not in check.stdout:raise RuntimeError('entrypoint_canary_failed')
    except BaseException:
        if current.resolve()==version:
            link=base/'current.rollback';link.symlink_to(previous);os.replace(link,current)
        raise
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=base/'backups'/stamp;backup.mkdir(parents=True,mode=0o700)
    record={'active':True,'version':digest,'previous_version':expected,'prior_files_sha256':actual,'launcher_sha256':sha(before),'launcher_changed':False,'native_binary_changed':False,'processes_signalled':False,'files_sha256':{n:sha(b) for n,b in bundle.items()}}
    atomic(backup/'activation.json',(json.dumps(record,indent=2)+'\n').encode())
    return record

def activate(root, source, node):
    root = root.resolve();launcher = root/'harmonika'
    before = launcher.read_bytes()
    if MARKER.encode() in before:
        raise RuntimeError('agent_entrypoint_already_installed')
    if not before.startswith(b'#!') or b'\n' not in before:
        raise RuntimeError('unsupported_launcher')
    bundle = {n: (source/n).read_bytes() for n in FILES}
    digest = sha(b''.join(bundle[n] for n in FILES))[:16]
    base = root/'agent-v32';base.mkdir(mode=0o700,exist_ok=True)
    version = base/digest;version.mkdir(mode=0o700,exist_ok=True)
    for name, data in bundle.items():
        file = version/name
        if file.exists() and file.read_bytes() != data:
            raise RuntimeError('version_collision')
        if not file.exists():file.write_bytes(data);file.chmod(0o600)
        subprocess.run([node,'--check',str(file)],check=True,capture_output=True)
    current=base/'current'
    previous=os.readlink(current) if current.is_symlink() else None
    if current.exists() and not current.is_symlink():raise RuntimeError('current_not_symlink')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=base/'backups'/stamp;backup.mkdir(parents=True,mode=0o700)
    (backup/'launcher').write_bytes(before);(backup/'launcher').chmod(0o600)
    first,rest=before.split(b'\n',1)
    branch=(MARKER+'if [ "${1-}" = "agent-v32" ]; then\n  shift\n  exec '+shlex.quote(node)+' '+shlex.quote(str(current/'agent-v32.js'))+' "$@"\nfi\n').encode()
    candidate=first+b'\n'+branch+rest
    if launcher.read_bytes()!=before:raise RuntimeError('launcher_compare_failed')
    link=base/'current.v32-new';link.symlink_to(version);os.replace(link,current)
    try:
        atomic(launcher,candidate,launcher.stat().st_mode&0o777)
        check=subprocess.run([str(launcher),'agent-v32','--help'],capture_output=True,text=True,timeout=10)
        if check.returncode or 'agent-v32' not in check.stdout:raise RuntimeError('entrypoint_canary_failed')
    except BaseException:
        if launcher.read_bytes()==candidate:atomic(launcher,before,0o755)
        if current.is_symlink() and current.resolve()==version:
            if previous is None:current.unlink()
            else:
                link=base/'current.rollback';link.symlink_to(previous);os.replace(link,current)
        raise
    record={'active':True,'version':digest,'launcher_sha256':sha(candidate),'prior_launcher_sha256':sha(before),'backup':str(backup),'native_binary_changed':False,'processes_signalled':False,'files_sha256':{n:sha(b) for n,b in bundle.items()}}
    atomic(backup/'activation.json',(json.dumps(record,indent=2)+'\n').encode())
    return record

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path.home()/'Projects/harmonika-engine');p.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1]/'tools');p.add_argument('--replace-version');p.add_argument('--expected-active-files',type=Path);a=p.parse_args()
    node=shutil.which('node')
    if not node:raise RuntimeError('node_unavailable')
    print(json.dumps(upgrade(a.root,a.source,node,a.replace_version,json.loads(a.expected_active_files.read_text()) if a.expected_active_files else None) if a.replace_version else activate(a.root,a.source,node)))

if __name__=='__main__':main()
