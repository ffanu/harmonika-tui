"""Private bounded subprocess/snapshot helpers for the operations adapters."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import threading
import time

BLOCKED = {'node_modules', 'vendor', '__pycache__', 'venv', 'private', 'secrets', 'credentials', 'backups', 'logs', 'data', 'uploads'}
SECRET = re.compile(r'(^\.|(^|[-_.])(secret|credential|password|passwd|token|id_rsa|id_ed25519|service.account)([-_.]|$))', re.I)
SUFFIXES = {'.py','.js','.jsx','.ts','.tsx','.mjs','.cjs','.go','.rs','.php','.rb','.java','.kt','.kts','.cs','.c','.h','.cpp','.json','.yaml','.yml','.toml','.lock','.txt','.xml','.tf','.html','.htm','.css','.scss','.svg','.png','.jpg','.jpeg','.webp','.gif','.ico','.woff','.woff2','.md','.sh','.sql'}


def regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def checked(path, directory=False):
    path = Path(path)
    if not path.is_absolute(): raise ValueError('absolute_operator_path_required')
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('symlink_path_denied')
    if directory and not path.is_dir(): raise ValueError('project_directory_required')
    return path


def relative(value, allow_dot=False):
    if not isinstance(value, str) or not value or len(value)>400 or '\\' in value or ':' in value or '\0' in value:
        raise ValueError('invalid_relative_path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or (str(path)=='.' and not allow_dot): raise ValueError('invalid_relative_path')
    if any(part.casefold() in BLOCKED or SECRET.search(part) for part in path.parts): raise ValueError('private_path_denied')
    return path


def project(config, selected='.'):
    root = checked(config.get('project_root', ''), directory=True)
    child = root / relative(selected, allow_dot=True)
    checked(child)
    if not child.is_relative_to(root): raise ValueError('project_scope_denied')
    return root, child


def artifact_root(config):
    root = Path(config.get('artifacts_root', ''))
    if not root.is_absolute() or len(root.parts)<3 or root in (Path.home(), Path.home().parent): raise ValueError('invalid_artifact_root')
    project_root=Path(config.get('project_root','/nonexistent')).absolute()
    if root==project_root or project_root in root.parents:raise ValueError('artifacts_inside_project_denied')
    if root.exists(): checked(root, directory=True)
    else:
        checked(root.parent, directory=True)
        root.mkdir(mode=0o700)
    if os.name=='nt':raise ValueError('private_artifact_acl_not_verified')
    if os.name!='nt':
        info=root.stat()
        if info.st_uid!=os.getuid() or info.st_mode&0o077: raise ValueError('artifact_root_not_private')
    return root


def snapshot(root, selected, destination):
    """Copy safe regular files, not links; no project configuration or source runs."""
    files=[];skipped=0;total=0
    if not selected.is_dir(): raise ValueError('scan_directory_required')
    for directory, dirs, names in os.walk(selected, followlinks=False):
        parent=Path(directory);checked(parent, directory=True)
        retained=[]
        for name in sorted(dirs):
            child=parent/name
            if child.is_symlink() or getattr(child.lstat(),'st_file_attributes',0)&0x400: raise ValueError('symlink_path_denied')
            if name.casefold() in BLOCKED or SECRET.search(name): skipped+=1
            else: retained.append(name)
        dirs[:]=retained
        for name in sorted(names):
            source=parent/name
            info=source.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400: raise ValueError('symlink_path_denied')
            if not regular(info): raise ValueError('special_file_denied')
            if SECRET.search(name) or (source.suffix.lower() not in SUFFIXES and name not in ('Dockerfile','Containerfile','Gemfile','go.mod','go.sum','Cargo.lock')):
                skipped+=1;continue
            if info.st_size>4*1024*1024 or len(files)>=512 or total+info.st_size>32*1024*1024: raise ValueError('project_snapshot_budget_exceeded')
            descriptor=os.open(source,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            with os.fdopen(descriptor,'rb') as stream:
                before=os.fstat(stream.fileno())
                if not regular(before) or (before.st_dev,before.st_ino)!=(info.st_dev,info.st_ino): raise ValueError('source_changed_during_snapshot')
                content=stream.read(4*1024*1024+1)
                after=os.fstat(stream.fileno())
            if len(content)>4*1024*1024 or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise ValueError('source_changed_during_snapshot')
            rel=source.relative_to(root).as_posix();target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with open(target,'xb') as stream: stream.write(content)
            target.chmod(0o400);files.append(rel);total+=len(content)
    if not files: raise ValueError('no_supported_project_files')
    return {'files':files,'files_copied':len(files),'files_skipped':skipped,'bytes_copied':total}


def tool(config, name):
    value=config.get('tools',{}).get(name)
    if not isinstance(value,str) or not Path(value).is_absolute(): return None
    path=Path(value)
    if not path.is_file() or (os.name!='nt' and not os.access(path,os.X_OK)): return None
    return str(path.resolve())


def environment(home=None):
    allowed=('PATH','SystemRoot','WINDIR','TEMP','TMP','HOME','USERPROFILE','PLAYWRIGHT_BROWSERS_PATH')
    env={key:os.environ[key] for key in allowed if key in os.environ}
    env.update(PYTHONIOENCODING='utf-8',NO_COLOR='1',SEMGREP_SEND_METRICS='off',SEMGREP_ENABLE_VERSION_CHECK='0',SEMGREP_LOG_SRCS='0',TRIVY_DISABLE_TELEMETRY='true',TRIVY_SKIP_VERSION_CHECK='true')
    if home: env.update(HOME=str(home),USERPROFILE=str(home),XDG_CONFIG_HOME=str(home),XDG_CACHE_HOME=str(home))
    return env


def execute(argv, *, cwd, data=None, timeout=60, env=None, maximum=4*1024*1024, watch=None):
    """Only this invocation's owned child/session can be stopped on limits."""
    try:
        proc=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env or environment(),start_new_session=os.name!='nt')
    except OSError:
        raise ValueError('adapter_execution_failed') from None
    buffers=[bytearray(),bytearray()];limited=threading.Event()
    def stop():
        if proc.poll() is not None:return
        try:
            if os.name!='nt' and os.getpgid(proc.pid)==proc.pid:os.killpg(proc.pid,signal.SIGKILL)
            else:proc.kill()
        except (OSError,ProcessLookupError):pass
    def read(stream,index,budget):
        try:
            while True:
                chunk=stream.read(8192)
                if not chunk:break
                if len(buffers[index])+len(chunk)>budget:limited.set();stop();break
                buffers[index].extend(chunk)
        finally:stream.close()
    readers=[threading.Thread(target=read,args=(proc.stdout,0,maximum),daemon=True),threading.Thread(target=read,args=(proc.stderr,1,65536),daemon=True)]
    for reader in readers:reader.start()
    try:
        if data is not None:
            try:proc.stdin.write(json.dumps(data,ensure_ascii=False).encode('utf-8'));proc.stdin.close()
            except BrokenPipeError:pass
        deadline=time.monotonic()+timeout
        while proc.poll() is None:
            if time.monotonic()>=deadline:raise ValueError('adapter_timeout')
            if watch is not None and not watch():raise ValueError('adapter_artifact_limit')
            time.sleep(0.05)
        for reader in readers:reader.join(timeout=3)
        if limited.is_set():raise ValueError('adapter_output_limit')
        if any(reader.is_alive() for reader in readers):raise ValueError('adapter_child_did_not_close')
        return proc.returncode,bytes(buffers[0])
    except subprocess.TimeoutExpired:
        raise ValueError('adapter_timeout') from None
    except OSError:
        raise ValueError('adapter_execution_failed') from None
    finally:
        stop()
        try:proc.wait(timeout=3)
        except subprocess.TimeoutExpired:pass
