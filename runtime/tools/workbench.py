"""Harmonika-owned terminal workbench; branded native executable stays unchanged."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

TOOLS=Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:sys.path.insert(0,str(TOOLS))
from workbench_model import read_context,load_sample,status_line


def migrate_options(config):
    """Update only UI/lifecycle preferences; credentials/guards are untouched."""
    result=json.loads(json.dumps(config));options=result.setdefault('options',{})
    options['auto_lsp']=True;options['progress']=True
    disabled=options.setdefault('disabled_skills',[])
    for name in ('harmonika-config','harmonika-hooks'):
        if name not in disabled:disabled.append(name)
    tui=options.setdefault('tui',{})
    tui.update(diff_mode='split',scrollbar='always',mouse=True)
    return result


def tmux_binary():
    if platform.system()!='Darwin' or platform.machine()!='arm64':return None
    p=TOOLS/'workbench-tmux';metadata=TOOLS/'workbench-runtime.json'
    if p.is_symlink() or not p.is_file() or not metadata.is_file():return None
    try:
        expected=json.loads(metadata.read_text())['binary']['sha256']
        if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:return None
        if p.stat().st_uid!=os.getuid():return None
        p.chmod(0o700)
        return str(p)
    except (OSError,ValueError,KeyError):return None


def native_scope(args,cwd):
    project=Path(cwd).resolve();directory=None;session=None
    for i,arg in enumerate(args):
        if arg=='--cwd' and i+1<len(args):project=Path(args[i+1]).expanduser().resolve()
        elif arg.startswith('--cwd='):project=Path(arg.split('=',1)[1]).expanduser().resolve()
        elif arg=='--data-dir' and i+1<len(args):directory=args[i+1]
        elif arg.startswith('--data-dir='):directory=arg.split('=',1)[1]
        elif arg in ('-s','--session') and i+1<len(args):session=args[i+1]
    path=Path(directory).expanduser() if directory else project/'.harmonika'
    if not path.is_absolute():path=project/path
    return str(project),str(path.resolve()),session


def command(context,kind):
    return shlex.join([sys.executable,'-I',str(TOOLS/'workbench.py'),kind,'--context',str(context)])


def tmux_configuration(context):
    status=command(context,'status')
    compact=shlex.join([sys.executable,'-I',str(TOOLS/'workbench.py'),'status','--compact','--context',str(context)])
    lines=['set -g status on','set -g status-interval 5','set -g status-position bottom',
           'set -g status-style "bg=#0a0e14,fg=#59c2ff"',
           'set -g status-left "#{?#{<:#{client_width},100}, Harmonika AI , Harmonika AI | Chat }"','set -g status-left-length 23',
           'set -g status-right-length 110','set -g status-right '+json.dumps('#{?#{<:#{client_width},110},#('+compact+'),#('+status+')}'),
           'set -g window-status-format ""','set -g window-status-current-format ""',
           'set -g default-terminal "xterm-256color"',
           'set -as terminal-features ",xterm*:RGB,screen*:RGB"',
           'set -g allow-passthrough on','set -g mouse on','set -g set-clipboard external',
           'set -g exit-empty on','set -g escape-time 10','set -g history-limit 100',
           'set -g automatic-rename off','set -g renumber-windows on',
           'set -g default-shell "/bin/sh"',
           'set -g pane-border-style "fg=#253648"','set -g pane-active-border-style "fg=#59c2ff"',
           'set -g prefix None','unbind-key C-b']
    for key,kind in [('F1','help'),('F2','metrics'),('F3','events'),('F5','diff'),('F7','terminal'),('F8','jobs')]:
        lines.append('bind-key -n '+key+' display-popup -E -w 90% -h 85% '+json.dumps(command(context,kind)))
    lines.append('bind-key -n F4 run-shell -b '+json.dumps(command(context,'toggle-files')))
    lines.append('bind-key -n F11 resize-pane -Z')
    return '\n'.join(lines)+'\n'


def start(binary,args,config,data,detached=False,offline=False):
    if not binary or not Path(binary).is_file():raise ValueError('workbench_binary_required')
    tmux=tmux_binary()
    if not tmux:
        if detached:raise ValueError('workbench_runtime_unavailable')
        os.execve(binary,['harmonika',*args],dict(os.environ))
    project,native,session=native_scope(args,os.getcwd())
    if not Path(project).is_dir():raise ValueError('project_directory_unavailable')
    cache=Path(config)/'workbench-state';cache.mkdir(mode=0o700,exist_ok=True);cache.chmod(0o700)
    sweep_cache(cache,tmux)
    instance=Path(tempfile.mkdtemp(dir=cache,prefix='instance-'));instance.chmod(0o700)
    context=instance/'context.json';socket='harmonika-workbench-'+str(os.getpid())+'-'+instance.name[-6:]
    value={'version':1,'project':project,'native_dir':native,'session':session,
        'config':str(Path(config).resolve()),'global_data':str(Path(data).resolve()),
        'socket':socket,'tmux':tmux,'offline':offline,
        'allow_discovery':not offline and Path(config).resolve()==(Path.home()/'.config/harmonika').resolve()}
    fd=os.open(context,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f)
    cfg=instance/'tmux.conf';cfg.write_text(tmux_configuration(context));cfg.chmod(0o600)
    env=dict(os.environ);env.pop('TMUX',None)
    # A fresh tmux server has no inherited shell update hooks, profiles or sessions.
    argv=[tmux,'-u','-L',socket,'-f',str(cfg),'new-session','-s','harmonika','-n','Chat','-c',project]
    if detached:argv+=['-d','-x','150','-y','42']
    argv.append(shlex.join([binary,*args]))
    result=subprocess.run(argv,env=env,cwd=project)
    if detached:
        if result.returncode:raise ValueError('workbench_start_failed')
        return {'ok':True,'context':str(context),'socket':socket,'tmux':tmux}
    # Never terminate a detached session or a user-owned native child here.
    return result.returncode


def sweep_cache(cache,tmux):
    """Prune only old inactive metadata directories; never stop a server/session."""
    entries=sorted((p for p in cache.glob('instance-*') if p.is_dir() and not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)
    for index,p in enumerate(entries):
        try:
            age=time.time()-p.stat().st_mtime
            if age<60 or (index<32 and age<86400):continue
            context=read_context(p/'context.json');socket=context.get('socket','')
            if not re.fullmatch(r'harmonika-workbench-\d+-[A-Za-z0-9_]{1,12}',socket):continue
            probe=subprocess.run([tmux,'-L',socket,'has-session'],capture_output=True,timeout=2)
            if probe.returncode==1:shutil.rmtree(p)
        except (OSError,ValueError,subprocess.SubprocessError):continue


def tmux_call(context,*args,capture=False):
    argv=[context['tmux'],'-L',context['socket'],*args]
    return subprocess.run(argv,capture_output=capture,text=capture,timeout=5)


def toggle_files(context_file):
    context=read_context(context_file)
    listed=tmux_call(context,'list-panes','-a','-F','#{pane_id}|#{window_id}|#{@harmonika_file_panel}',capture=True)
    existing=[line.split('|') for line in listed.stdout.splitlines() if line.endswith('|on')]
    if existing:
        # Hiding a panel preserves a manually opened editor and unsaved changes.
        window=tmux_call(context,'display-message','-p','#{window_id}',capture=True).stdout.strip()
        if existing[0][1]==window:tmux_call(context,'break-pane','-d','-s',existing[0][0],'-n','Files')
        else:tmux_call(context,'join-pane','-h','-l','42%','-s',existing[0][0],'-t',window)
        return
    width=tmux_call(context,'display-message','-p','#{window_width}',capture=True).stdout.strip()
    if int(width or 0)<110:tmux_call(context,'display-popup','-E','-w','95%','-h','90%',command(context_file,'files'));return
    result=tmux_call(context,'split-window','-h','-l','42%','-c',context['project'],'-P','-F','#{pane_id}',command(context_file,'files'),capture=True)
    if result.returncode==0:
        tmux_call(context,'select-pane','-t',result.stdout.strip(),'-T','Harmonika Files')
        tmux_call(context,'set-option','-p','-t',result.stdout.strip(),'@harmonika_file_panel','on')


def run_diff(context):
    # No shell, external diff driver, textconv, writes, discard or auto-apply.
    result=subprocess.run(['git','-c','core.fsmonitor=false','--no-pager','diff','--no-ext-diff','--no-textconv','--color=always'],cwd=context['project'],capture_output=True)
    if result.returncode:
        print('Harmonika AI · Diff\nFolder ini bukan repositori Git yang dapat dibaca.\nTekan Enter untuk kembali.');input();return
    if not result.stdout:
        print('Harmonika AI · Diff\nTidak ada perubahan working tree yang dapat ditampilkan.\nTekan Enter untuk kembali.');input();return
    pager=subprocess.Popen(['/usr/bin/less','-R'],stdin=subprocess.PIPE)
    try:pager.communicate(result.stdout[:2*1024*1024])
    except BrokenPipeError:pass


def main():
    argv=sys.argv[1:];native_args=[]
    if '--' in argv:
        split=argv.index('--');native_args=argv[split+1:];argv=argv[:split]
    parser=argparse.ArgumentParser(description='Harmonika terminal workbench')
    parser.add_argument('action',choices=['start','status','help','metrics','events','jobs','files','toggle-files','diff','terminal'])
    parser.add_argument('--context');parser.add_argument('--binary');parser.add_argument('--config');parser.add_argument('--data')
    parser.add_argument('--detached-test',action='store_true');parser.add_argument('--offline',action='store_true')
    parser.add_argument('--compact',action='store_true')
    args=parser.parse_args(argv)
    if args.action=='start':
        value=start(args.binary,native_args,args.config,args.data,args.detached_test,args.offline)
        if isinstance(value,dict):print(json.dumps(value));return
        raise SystemExit(value)
    if not args.context:raise ValueError('workbench_context_required')
    context=read_context(args.context)
    if args.action=='status':
        print(status_line(load_sample(args.context,refresh=True),args.compact));return
    if args.action=='toggle-files':toggle_files(args.context);return
    if args.action=='diff':run_diff(context);return
    if args.action=='terminal':
        shell=os.environ.get('SHELL','/bin/zsh')
        if not Path(shell).is_absolute() or not Path(shell).is_file():shell='/bin/sh'
        raise SystemExit(subprocess.run([shell],cwd=context['project']).returncode)
    from workbench_ui import run_panel
    run_panel(args.action,args.context)


if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,EOFError):pass
    except Exception as error:
        # Never print an OS exception containing project paths or configuration.
        print('Harmonika AI: panel belum tersedia ('+type(error).__name__+').',file=sys.stderr)
        raise SystemExit(1)
