"""Build a private terminal compositor; never alter the branded native binary."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path.home()/'.config/harmonika-terminal/workbench-build'
PREFIX=ROOT/'prefix'
ARTIFACTS=ROOT/'artifacts'
SOURCES=[('libevent-2.1.12-stable','https://github.com/libevent/libevent/releases/download/release-2.1.12-stable/libevent-2.1.12-stable.tar.gz'),
         ('utf8proc-2.10.0','https://github.com/JuliaStrings/utf8proc/archive/refs/tags/v2.10.0.tar.gz'),
         ('tmux-3.5a','https://github.com/tmux/tmux/releases/download/3.5a/tmux-3.5a.tar.gz')]


def run(argv,cwd,env=None):
    with (ROOT/'build.log').open('ab') as out:
        result=subprocess.run(argv,cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError('workbench_dependency_build_failed:'+str(result.returncode))


def main():
    ROOT.mkdir(parents=True,exist_ok=True);ARTIFACTS.mkdir(exist_ok=True);manifest={}
    for name,url in SOURCES:
        archive=ROOT/(name+'.tar.gz')
        if not archive.exists():subprocess.run(['curl','--fail','--location','--silent','--show-error','--max-time','120','--output',str(archive),url],check=True)
        manifest[name]={'url':url,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
        target=ROOT/name
        if not target.exists():
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    p=Path(member.name)
                    if p.is_absolute() or '..' in p.parts or member.issym() or member.islnk():raise RuntimeError('unsafe_source_archive')
                tar.extractall(ROOT)
    lib=ROOT/'libevent-2.1.12-stable'
    # Recent macOS SDKs expose pipe2 as a weak symbol on some hosts. Explicitly
    # select libevent's POSIX pipe fallback, avoiding a call through a null stub.
    portable_env=dict(os.environ,MACOSX_DEPLOYMENT_TARGET='11.0',ac_cv_func_pipe2='no')
    if not (PREFIX/'lib/libevent.a').exists() or not (ROOT/'portable-pipe.ok').exists():
        run(['./configure','--prefix='+str(PREFIX),'--disable-shared','--enable-static','--disable-openssl','--disable-libevent-regress','--disable-samples'],lib,portable_env)
        run(['make','-j','4'],lib);run(['make','install'],lib)
        (ROOT/'portable-pipe.ok').write_text('pipe fallback selected\n')
    utf8=ROOT/'utf8proc-2.10.0'
    run(['make','libutf8proc.a'],utf8)
    shutil.copy2(utf8/'libutf8proc.a',PREFIX/'lib/libutf8proc.a')
    shutil.copy2(utf8/'utf8proc.h',PREFIX/'include/utf8proc.h')
    tmux=ROOT/'tmux-3.5a'
    env=dict(portable_env,CFLAGS='-O2 -I'+str(PREFIX/'include'),LDFLAGS='-L'+str(PREFIX/'lib'),
             LIBUTF8PROC_CFLAGS='-I'+str(PREFIX/'include'),LIBUTF8PROC_LIBS=str(PREFIX/'lib/libutf8proc.a'))
    run(['./configure','--prefix='+str(PREFIX),'--enable-utf8proc'],tmux,env);run(['make','-j','4'],tmux,env)
    shutil.copy2(tmux/'tmux',ARTIFACTS/'workbench-tmux');(ARTIFACTS/'workbench-tmux').chmod(0o755)
    result=subprocess.check_output([str(ARTIFACTS/'workbench-tmux'),'-V'],text=True).strip()
    manifest['binary']={'version':result,'sha256':hashlib.sha256((ARTIFACTS/'workbench-tmux').read_bytes()).hexdigest()}
    licenses=[]
    for p in [lib/'LICENSE',utf8/'LICENSE.md',tmux/'COPYING']:
        if p.exists():licenses.append(p.parent.name+'\n'+p.read_text())
    (ARTIFACTS/'workbench-thirdparty.txt').write_text('\n\n'.join(licenses))
    (ARTIFACTS/'workbench-runtime.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'built':True,'artifact':str(ARTIFACTS/'workbench-tmux'),'version':result}))


if __name__=='__main__':main()
