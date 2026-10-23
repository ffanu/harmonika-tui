#!/usr/bin/env python3
"""Record a real, isolated Harmonika PTY and render its ANSI screen to a GIF.

Requires pyte and Pillow. Uses the installed application, not a simulated UI.
No prompts, credentials, customer data or existing sessions are captured.
"""
import codecs
import fcntl
import math
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import tempfile
import termios
import time
import pyte
from PIL import Image,ImageDraw,ImageFont

COLS,ROWS=120,34
OUTPUT=Path(__file__).resolve().parent
FONT_PATH='/System/Library/Fonts/Menlo.ttc'
PALETTE={'black':'#1d2021','red':'#fb4934','green':'#b8bb26','brown':'#fabd2f','yellow':'#fabd2f','blue':'#83a598','magenta':'#d3869b','cyan':'#8ec07c','white':'#ebdbb2','brightblack':'#928374','brightred':'#fb4934','brightgreen':'#b8bb26','brightyellow':'#fabd2f','brightblue':'#83a598','brightmagenta':'#d3869b','brightcyan':'#8ec07c','brightwhite':'#fbf1c7'}
class CaptureScreen(pyte.Screen):
    def select_graphic_rendition(self,*attributes,private=False):
        # Terminal capability queries are not visual SGR styling.
        if not private:return super().select_graphic_rendition(*attributes)
def color(value,default):
    if value=='default':return default
    if len(value)==6 and all(c in '0123456789abcdefABCDEF' for c in value):return '#'+value
    return PALETTE.get(value,default)

def main():
    executable=shutil.which('harmonika')
    if not executable:raise RuntimeError('Install Harmonika before recording')
    if not Path(FONT_PATH).is_file():raise RuntimeError('Set a local monospace font for this platform')
    font=ImageFont.truetype(FONT_PATH,14);titlefont=ImageFont.truetype(FONT_PATH,12)
    cw,ch=math.ceil(font.getlength('M')),19
    screen=CaptureScreen(COLS,ROWS);stream=pyte.Stream(screen)
    frames=[];decoder=codecs.getincrementaldecoder('utf8')('replace')
    with tempfile.TemporaryDirectory(prefix='harmonika-demo-',dir='/tmp') as directory:
        root=Path(directory)
        (root/'AGENTS.md').write_text('Isolated visual demo. Do not call providers or edit project files.\n')
        master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',ROWS,COLS,0,0))
        child=os.fork()
        if child==0:
            os.setsid();fcntl.ioctl(slave,termios.TIOCSCTTY,0)
            for descriptor in (0,1,2):os.dup2(slave,descriptor)
            os.close(master);os.close(slave);os.chdir(root)
            env=dict(os.environ,TERM='xterm-256color',PS1='harmonika@terminal % ',HISTFILE='/dev/null')
            env.pop('NO_COLOR',None)
            env.update(FORCE_COLOR='3',COLORTERM='truecolor')
            os.execve('/bin/zsh',['zsh','-f'],env)
        os.close(slave)
        def picture():
            image=Image.new('RGB',(COLS*cw+48,ROWS*ch+66),'#111820');draw=ImageDraw.Draw(image)
            for x,c in [(22,'#ff5f57'),(42,'#febc2e'),(62,'#28c840')]:draw.ellipse((x,16,x+10,26),fill=c)
            draw.text((90,13),'Harmonika TUI · Live terminal capture',font=titlefont,fill='#a7b9c9')
            for y in range(ROWS):
                for x in range(COLS):
                    cell=screen.buffer[y][x];fg=color(cell.fg,'#e4e9ef');bg=color(cell.bg,'#111820')
                    if cell.reverse:fg,bg=bg,fg
                    left,top=24+x*cw,46+y*ch
                    if bg!='#111820':draw.rectangle((left,top,left+cw,top+ch),fill=bg)
                    if cell.data.strip():draw.text((left,top),cell.data,font=font,fill=fg)
            return image
        def collect(seconds,record=True):
            until=time.monotonic()+seconds;next_frame=0
            while time.monotonic()<until:
                ready,_,_=select.select([master],[],[],0.05)
                if ready:
                    try:data=os.read(master,65536)
                    except OSError:break
                    if not data:break
                    stream.feed(decoder.decode(data))
                if record and time.monotonic()>=next_frame:
                    frames.append(picture());next_frame=time.monotonic()+0.20
        def send(value):os.write(master,value.encode())
        try:
            collect(.4,False)
            # Only the bare app invocation is isolated; status commands stay real.
            function=f'''function harmonika() {{ if (( $# )); then command {executable} "$@"; else command {executable} --host unix://{root}/demo.sock --data-dir {root}/data --cwd {root}; fi; }}\n'''
            send(function);collect(.3,False);send('clear\n');collect(.3,False);screen.reset()
            send('harmonika --version\n');collect(1.4)
            send('harmonika doctor\n');collect(3.0)
            send('clear\n');collect(.3,False);screen.reset()
            send('harmonika\n');collect(5.5)
            OUTPUT.mkdir(parents=True,exist_ok=True)
            picture().save(OUTPUT/'harmonika-terminal.png')
            send('\x03');collect(.25,False);send('y');collect(.5,False);send('exit\n');collect(.3,False)
        finally:
            os.close(master)
            os.waitpid(child,0)
        converted=[frame.quantize(colors=96) for frame in frames]
        converted[0].save(OUTPUT/'harmonika-demo.gif',save_all=True,append_images=converted[1:],duration=200,loop=0,optimize=True)
        print(f'Recorded {len(frames)} real terminal frames.')

if __name__=='__main__':main()
