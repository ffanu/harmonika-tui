"""Activate a signed incremental TUI release; preserve every active user process."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import sys

SOURCE=Path(__file__).resolve().parents[1]


def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def atomic(path,content,mode=0o600):
    path=path.resolve();fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.harmonika-workbench-')
    try:
        with os.fdopen(fd,'wb') as out:out.write(content);out.flush();os.fsync(out.fileno())
        os.chmod(temp,mode);os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def main():
    release=sys.argv[1];runtime=module(SOURCE/'runtime/harmonika.py','activate_workbench_runtime')
    wb=module(SOURCE/'runtime/tools/workbench.py','activate_workbench_config')
    current,manifest=runtime.active();runtime.verify_files(current,manifest)
    launcher=runtime.ROOT/'launcher.py'
    if runtime.digest(launcher)!=manifest['files']['tools/harmonika.py']['sha256']:raise ValueError('installed_launcher_changed')
    binary=current/'harmonika';binary_before=runtime.digest(binary)
    smoke=json.loads((SOURCE/'docs/TUI-WORKBENCH-SMOKE.json').read_text())
    if not smoke.get('ok') or smoke.get('binary_sha256')!=binary_before:raise ValueError('passing_native_smoke_required')
    backup=runtime.ROOT/('workbench-backup-'+release)
    backup.mkdir(mode=0o700,exist_ok=False)
    shutil.copy2(launcher,backup/'launcher.py');(backup/'launcher.py').chmod(0o600)
    config=Path.home()/'.config/harmonika';share=Path.home()/'.local/share/harmonika/crush.json'
    paths=[config/'harmonika.json',config/'crush.json',share]
    originals={str(p):p.read_bytes() for p in paths if p.is_file()}
    for i,(path,raw) in enumerate(originals.items()):
        (backup/('config-'+str(i)+'.json')).write_bytes(raw);(backup/('config-'+str(i)+'.json')).chmod(0o600)
    (backup/'config-paths.json').write_text(json.dumps(list(originals)));(backup/'config-paths.json').chmod(0o600)
    result=runtime.install_package(SOURCE/'dist'/('harmonika-terminal-'+release+'.zip'))
    try:
        installed,new_manifest=runtime.active();runtime.verify_files(installed,new_manifest)
        primary=json.loads(originals[str(config/'harmonika.json')]);updated=wb.migrate_options(primary)
        for key in ('providers','models','permissions','hooks','mcp','lsp'):
            if updated.get(key)!=primary.get(key):raise ValueError('unrelated_config_changed')
        for p in [config/'harmonika.json',config/'crush.json']:
            atomic(p,(json.dumps(updated,indent=2)+'\n').encode())
        if str(share) in originals:
            settings=json.loads(originals[str(share)]);options=settings.setdefault('options',{})
            options['auto_lsp']=True;options['progress']=True;options.setdefault('tui',{})['diff_mode']='split'
            atomic(share,(json.dumps(settings,indent=2)+'\n').encode())
        atomic(launcher,(installed/'tools/harmonika.py').read_bytes())
        if runtime.digest(installed/'harmonika')!=binary_before:raise ValueError('native_binary_changed')
    except Exception:
        runtime.switch(current.name)
        for p,raw in originals.items():atomic(Path(p),raw)
        atomic(launcher,(backup/'launcher.py').read_bytes())
        raise
    report={**result,'native_binary_unchanged':True,'native_sha256':binary_before,
        'ascii_brand_preserved':True,'active_user_processes_signalled':False,'automatic_upstream_update':False,
        'lsp_auto_start':True,'incompatible_config_hooks_skills_disabled':True,
        'config_backup_private':str(backup),'rollback':'harmonika rollback',
        'launch':'harmonika (new terminal session)','native_escape_hatch':'harmonika --no-workbench'}
    (SOURCE/'docs/TUI-WORKBENCH-ACTIVATION.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
