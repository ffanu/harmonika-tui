"""Refresh only C-v/F6 on owned Harmonika workbenches; never attach or send keys."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_runtime',ROOT/'runtime/harmonika.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
current,manifest=r.active();r.verify_files(current,manifest)
updated=0
for context in (Path.home()/'.config/harmonika/workbench-state').glob('instance-*/context.json'):
    if context.is_symlink() or context.stat().st_uid!=os.getuid() or context.stat().st_mode & 0o077:continue
    value=json.loads(context.read_text())
    socket=value.get('socket','');tmux=Path(value.get('tmux',''))
    if not re.fullmatch(r'harmonika-workbench-\d+-[A-Za-z0-9_]{1,12}',socket):continue
    if not tmux.is_file() or tmux.stat().st_uid!=os.getuid():continue
    base=[str(tmux),'-L',socket]
    if subprocess.run([*base,'has-session'],capture_output=True,timeout=2).returncode:continue
    cmd=shlex.join(['/usr/bin/python3','-I',str(current/'tools/workbench.py'),
                    'paste-path','--context',str(context)])+" --pane '#{pane_id}'"
    for key in ('C-v','F6'):
        subprocess.run([*base,'bind-key','-n',key,'run-shell',cmd],check=True,capture_output=True,timeout=3)
        result=subprocess.run([*base,'list-keys','-T','root',key],check=True,capture_output=True,text=True,timeout=3)
        if str(current/'tools/workbench.py') not in result.stdout:raise RuntimeError('binding_verification_failed')
    updated+=1
print(json.dumps({'release':manifest['release'],'workbenches_updated':updated,
                  'keys':['C-v','F6'],'user_keys_sent':False,'sessions_stopped':False}))
