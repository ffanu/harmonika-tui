"""Keyboard-first read-only panels around the existing Harmonika native TUI."""
import curses
from datetime import datetime
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

if __package__:
    from .workbench_model import clean,read_context,load_sample,sparkline
else:
    from workbench_model import clean,read_context,load_sample,sparkline

SKIP={'.git','.harmonika','.crush','.codex','.ssh','node_modules','__pycache__','.venv','venv','dist','target'}
PRIVATE=re.compile(r'(^\.env($|\.)|(^|[_.-])(credentials?|secrets?|cookies?|private[-_]?key|id_rsa|id_ed25519)([_.-]|$)|\.(pem|key|p12|pfx)$)',re.I)


def safe_file(root,path):
    root=Path(root).resolve();path=Path(path)
    if path.is_symlink():raise ValueError('Berkas symlink tidak dibuka otomatis.')
    real=path.resolve()
    try:relative=real.relative_to(root)
    except ValueError:raise ValueError('Berkas berada di luar proyek.') from None
    if any(p in SKIP or PRIVATE.search(p) for p in relative.parts):raise ValueError('Berkas privat disembunyikan.')
    if not real.is_file() or real.stat().st_size>262144:raise ValueError('Pratinjau maksimal 256 KiB.')
    return real


def project_files(root,limit=1200):
    root=Path(root).resolve();rows=[]
    for directory,folders,files in os.walk(root,followlinks=False):
        rel=Path(directory).relative_to(root)
        folders[:]=sorted(x for x in folders if x not in SKIP and not PRIVATE.search(x) and not (Path(directory)/x).is_symlink()) if len(rel.parts)<6 else []
        for name in sorted(files):
            p=Path(directory)/name
            if not p.is_symlink() and not PRIVATE.search(name):rows.append(str(p.relative_to(root)))
            if len(rows)>=limit:return rows
    return rows


def fuzzy(query,text):
    cursor=iter(text.lower())
    return all(any(ch==value for value in cursor) for ch in query.lower())


def code_lines(root,path):
    p=safe_file(root,path);raw=p.read_bytes()
    if b'\0' in raw[:4096]:raise ValueError('Berkas biner tidak ditampilkan sebagai teks.')
    return [re.sub(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]','',line.replace('\t','    '))[:1000]
            for line in raw.decode('utf-8',errors='replace').splitlines()[:3000]]


def draw(stdscr,y,x,text,width,style=0):
    try:
        if y>=0 and x>=0 and width>0:stdscr.addnstr(y,x,text,max(0,width),style)
    except curses.error:pass


def setup(stdscr):
    curses.curs_set(0);stdscr.keypad(True);stdscr.timeout(600)
    if curses.has_colors():
        curses.start_color();curses.use_default_colors()
        curses.init_pair(1,curses.COLOR_CYAN,-1);curses.init_pair(2,curses.COLOR_YELLOW,-1)
        curses.init_pair(3,curses.COLOR_GREEN,-1);curses.init_pair(4,curses.COLOR_RED,-1)


def color(n):return curses.color_pair(n) if curses.has_colors() else 0


def metrics_lines(data):
    lines=[]
    for key,title in [('cpu','CPU'),('ram','RAM (perkiraan)'),('disk','Disk')]:
        value=data.get(key)
        bar='—' if not isinstance(value,(int,float)) else '['+'█'*round(value/5)+'░'*(20-round(value/5))+'] '+str(round(value,1))+'%'
        lines.append(title.ljust(19)+bar)
    api=data.get('api',{});lines+=['','Engine Harmonika     '+str(api.get('status','memeriksa'))]
    if api.get('latency_ms') is not None:lines.append('Probe API            '+str(api['latency_ms'])+' ms')
    lines.append('Riwayat probe API    '+sparkline(data.get('latency_history',[])))
    lines.append('PC 2 Graphics        '+str(data.get('graphics','not_checked')))
    native=data.get('native',{});lines+=['','Metadata proyek','Sesi tersimpan       '+str(native.get('sessions',0)),
        'Respons berjalan     '+str(native.get('active_responses',0))]
    prompt,completion=native.get('prompt_tokens'),native.get('completion_tokens')
    lines.append('Token input/output   '+(f'{prompt or 0:,} / {completion or 0:,}' if prompt or completion else '— belum dilaporkan'))
    lines.append('Biaya tercatat       '+(f"${native['cost']:.4f}" if native.get('cost') else '— belum dilaporkan'))
    lines+=['','Probe API bukan waktu token pertama AI.','Token/biaya adalah metadata proyek, bukan tagihan.']
    return lines


def event_lines(data,level='ALL'):
    result=[]
    for item in data.get('events',[])[-100:]:
        if level!='ALL' and item.get('level')!=level:continue
        stamp=datetime.fromtimestamp(item.get('at',0)).strftime('%H:%M:%S')
        result.append(stamp+'  '+clean(item.get('level','INFO'),5).ljust(6)+clean(item.get('code','unknown'),80))
    for item in data.get('registry_errors',[]):
        if level not in ('ALL','ERROR'):continue
        result.append(clean(item.get('timestamp',''),30)+'  ERROR  '+clean(item.get('skill_id',''),90)+' · '+clean(item.get('error_type',''),40))
    return result[-100:] or ['Belum ada kejadian pada filter ini.']


HELP=['F2  Metrik perangkat dan koneksi','F3  Kejadian teknis (1 Semua · 2 Info · 3 Warn · 4 Error)',
      'F4  File + pratinjau kode','F5  Git diff (hanya lihat)','F7  Terminal proyek','F8  Respons/proses berjalan','F11 Zoom / Zen',
      '', 'Ctrl+P · Shift+Tab · input tetap mengikuti TUI native.',
      'File: / cari · Tab fokus · ↑↓ navigasi · Enter kode · c salin · e edit · r run · q tutup.',
      'Kode penuh memakai penomoran dan syntax highlighting dari bat.',
      'LSP native: diagnostics, symbols, definition dan references melalui tools chat.',
      'Rename/write tetap mengikuti guard proyek dan persetujuan native.',
      '', 'q / Esc menutup panel; chat tetap berjalan di belakang panel.']


def panel(stdscr,kind,context_file):
    setup(stdscr);scroll=0;level='ALL'
    while True:
        stdscr.erase();height,width=stdscr.getmaxyx()
        title={'metrics':'Metrik','events':'Kejadian','jobs':'Proses','help':'Panduan'}.get(kind,kind)
        draw(stdscr,0,1,'Harmonika AI · '+title,width-3,color(1)|curses.A_BOLD)
        data=load_sample(context_file)
        if kind=='metrics':lines=metrics_lines(data)
        elif kind=='events':lines=event_lines(data,level)
        elif kind=='jobs':
            n=data.get('native',{});active=n.get('active_responses',0)
            lines=['Respons AI berjalan: '+str(active),
                ('Durasi tercatat: '+str(n.get('response_age',0))+' detik') if active else 'Tidak ada respons aktif yang tercatat.',
                '', 'Detail tool dan persetujuan tersedia di chat.',
                'Persentase/ETA ditampilkan hanya jika engine menyediakannya.']
        else:lines=HELP
        scroll=min(max(0,scroll),max(0,len(lines)-max(1,height-4)))
        for i,line in enumerate(lines[scroll:scroll+max(0,height-4)]):draw(stdscr,i+2,1,line,width-3)
        draw(stdscr,height-1,1,'↑↓ PgUp/PgDn · q/Esc Tutup'+(' · 1 Semua 2 Info 3 Warn 4 Error' if kind=='events' else ''),width-3,color(2))
        stdscr.refresh();key=stdscr.getch()
        if key in (ord('q'),27):return
        if key in (curses.KEY_DOWN,ord('j')):scroll+=1
        if key in (curses.KEY_UP,ord('k')):scroll-=1
        if key==curses.KEY_NPAGE:scroll+=max(1,height-5)
        if key==curses.KEY_PPAGE:scroll-=max(1,height-5)
        if kind=='events' and key in map(ord,'1234'):level=['ALL','INFO','WARN','ERROR'][key-ord('1')];scroll=0


def files(stdscr,context_file):
    context=read_context(context_file);root=Path(context['project']).resolve();setup(stdscr)
    all_files=project_files(root);query='';search=False;selected=0;offset=0;focus=0;notice='';last_file=None;lines=[]
    while True:
        stdscr.erase();height,width=stdscr.getmaxyx();items=[p for p in all_files if fuzzy(query,p)]
        selected=min(max(0,selected),max(0,len(items)-1));split=max(16,min(width//3,38)) if width>=52 else width
        draw(stdscr,0,1,'Harmonika AI · File / Kode',width-3,color(1)|curses.A_BOLD)
        draw(stdscr,1,1,('/ '+query+('▌' if search else '')) if query or search else 'Proyek · '+clean(root.name),width-3,color(2))
        start=max(0,selected-max(1,height-7)//2)
        for i,name in enumerate(items[start:start+max(1,height-5)]):
            style=curses.A_REVERSE if i+start==selected else 0
            draw(stdscr,i+3,1,clean(name,300),split-3,style)
        chosen=root/items[selected] if items else None
        if chosen!=last_file:
            offset=0;last_file=chosen
            try:lines=code_lines(root,chosen) if chosen else ['Belum ada berkas cocok.']
            except ValueError as error:lines=[str(error)]
            except OSError:lines=['Berkas sedang berubah atau belum dapat dibaca.']
        if width>=52:
            for y in range(2,max(2,height-2)):draw(stdscr,y,split-1,'│',1,color(1))
            for i,line in enumerate(lines[offset:offset+max(1,height-5)]):
                draw(stdscr,i+3,split+1,str(i+offset+1).rjust(4)+' '+line,width-split-3,
                     color(2) if line.lstrip().startswith(('#','//')) else 0)
        draw(stdscr,height-2,1,notice or ('Fokus kode' if focus else 'Fokus file'),width-3,color(3))
        draw(stdscr,height-1,1,'/ Cari · Tab Fokus · Enter Kode · c Salin · e Edit · r Run · q Tutup',width-3,color(2));stdscr.refresh()
        key=stdscr.getch()
        if key==-1:continue
        notice=''
        if search:
            if key in (27,10,13):search=False
            elif key in (127,8,curses.KEY_BACKSPACE):query=query[:-1];selected=0
            elif 32<=key<127:query+=chr(key);selected=0
            continue
        if key in (27,ord('q')):return
        if key==ord('/') :search=True;continue
        if key==9:focus=1-focus
        if key in (curses.KEY_DOWN,ord('j')):
            if focus:offset=min(max(0,len(lines)-1),offset+1)
            else:selected+=1
        if key in (curses.KEY_UP,ord('k')):
            if focus:offset=max(0,offset-1)
            else:selected-=1
        if key==curses.KEY_NPAGE:offset=min(max(0,len(lines)-1),offset+max(1,height-6))
        if key==curses.KEY_PPAGE:offset=max(0,offset-max(1,height-6))
        if key in (10,13,ord('e'),ord('c'),ord('r')) and chosen:
            try:
                chosen=safe_file(root,chosen)
                if key==ord('c'):
                    if sys_platform()!='darwin':notice='Gunakan seleksi/copy terminal pada platform ini.';continue
                    subprocess.run(['/usr/bin/pbcopy'],input=chosen.read_bytes(),check=True,timeout=2)
                    notice='Kode disalin.';continue
                if key==ord('r'):
                    draw(stdscr,height-2,1,'Jalankan berkas proyek ini? y = Jalankan · tombol lain = Batal',width-3,color(2));stdscr.refresh();stdscr.timeout(-1)
                    approved=stdscr.getch()==ord('y');stdscr.timeout(600)
                    if not approved:notice='Dibatalkan.';continue
                    if chosen.suffix=='.py':command=['/usr/bin/python3',str(chosen)]
                    elif chosen.suffix in ('.js','.mjs','.cjs'):
                        node=shutil.which('node')
                        if not node:notice='Node belum tersedia.';continue
                        command=[node,str(chosen)]
                    else:notice='Run tersedia untuk Python/JavaScript.';continue
                elif key==ord('e'):
                    command=shlex.split(os.environ.get('EDITOR','/usr/bin/nano'))+[str(chosen)]
                else:
                    bat=shutil.which('bat') or str(Path.home()/'.local/bin/bat')
                    command=[bat,'--style=numbers,header','--color=always','--paging=always','--',str(chosen)] if Path(bat).is_file() else ['/usr/bin/less','-N','--',str(chosen)]
                curses.def_prog_mode();curses.endwin()
                try:
                    subprocess.run(command,cwd=root,env=dict(os.environ,BAT_PAGER='less -R',LESS='-R'))
                    if key==ord('r'):input('\nTekan Enter untuk kembali ke Harmonika AI.')
                finally:
                    curses.reset_prog_mode();stdscr.clear();last_file=None
            except (ValueError,OSError,subprocess.SubprocessError) as error:notice=str(error) if isinstance(error,ValueError) else 'Aksi belum dapat dijalankan.'


def sys_platform():
    import sys
    return sys.platform


def run_panel(kind,context):
    curses.wrapper(files if kind=='files' else panel,*([context] if kind=='files' else [kind,context]))
