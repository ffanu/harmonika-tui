"""PTY for the private tmux client: translate Finder Command+V into path paste.
Clipboard is consulted only for an explicit bracketed paste; nothing is logged.
"""
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import shlex
import signal
import termios
import tty
import time

START=b'\x1b[200~'
END=b'\x1b[201~'
LIMIT=1024*1024

class PasteFilter:
    def __init__(self, reader=None):
        if reader is None:
            from file_paste import read_clipboard_data
            reader=read_clipboard_data
        self.reader=reader
        self.pending=b''
        self.inside=False
        self.overflow=False

    def convert(self, payload):
        try:
            from file_paste import paste_text
            data=self.reader()
            if not data.get('paths'):return START+payload+END
            expected=paste_text(data)
            text=payload.decode('utf-8')
            paths=shlex.split(expected)
            candidates={expected, ' '.join(paths), '\n'.join(paths),
                        ' '.join(shlex.quote(p) for p in paths),
                        ' '.join(Path(p).name for p in paths)}
            try:matches=shlex.split(text)==paths
            except ValueError:matches=False
            if matches or text in candidates:
                # C-v is intercepted by our private tmux binding. It resolves
                # Finder file references and types safely quoted literal paths.
                return b'\x16'
        except Exception:pass
        return START+payload+END

    def feed(self, data):
        self.pending+=data;out=bytearray()
        while self.pending:
            marker=END if self.inside else START
            index=self.pending.find(marker)
            if index>=0:
                before=self.pending[:index];self.pending=self.pending[index+len(marker):]
                if self.inside:
                    out.extend(before+END if self.overflow else self.convert(before))
                    self.inside=False;self.overflow=False
                else:
                    out.extend(before);self.inside=True
                continue
            if self.inside and not self.overflow and len(self.pending)<=LIMIT:break
            keep=0
            for i in range(1,len(marker)):
                if self.pending.endswith(marker[:i]):keep=i
            chunk=self.pending[:-keep] if keep else self.pending
            if self.inside and not self.overflow:
                out.extend(START);self.overflow=True
            out.extend(chunk);self.pending=self.pending[-keep:] if keep else b''
            break
        return bytes(out)


def run_client(argv, env, cwd):
    """Relay only a newly spawned client; never attach to an existing writer."""
    original=termios.tcgetattr(0)
    pid,master=pty.fork()
    if pid==0:
        os.chdir(cwd)
        os.execvpe(argv[0],argv,env)
    def resize(*_):
        try:fcntl.ioctl(master,termios.TIOCSWINSZ,fcntl.ioctl(0,termios.TIOCGWINSZ,b'\0'*8))
        except OSError:pass
    previous=signal.signal(signal.SIGWINCH,resize)
    resize();filtered=PasteFilter();last_input=0.0
    def write(fd,data):
        view=memoryview(data)
        while view:view=view[os.write(fd,view):]
    try:
        tty.setraw(0)
        while True:
            ready,_,_=select.select([0,master],[],[],.025 if filtered.pending and not filtered.inside else .2)
            if 0 not in ready and filtered.pending and not filtered.inside and time.monotonic()-last_input>=.03:
                write(master,filtered.pending);filtered.pending=b''
            if master in ready:
                try:chunk=os.read(master,65536)
                except OSError as e:
                    if e.errno==errno.EIO:break
                    raise
                if not chunk:break
                write(1,chunk)
            if 0 in ready:
                chunk=os.read(0,65536)
                if not chunk:break
                last_input=time.monotonic()
                write(master,filtered.feed(chunk))
    finally:
        termios.tcsetattr(0,termios.TCSADRAIN,original)
        signal.signal(signal.SIGWINCH,previous)
        os.close(master)
    _,status=os.waitpid(pid,0)
    return os.waitstatus_to_exitcode(status)
