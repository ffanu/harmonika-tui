"""Private, bounded menu traversal. Never equate a discovered URL with a visit."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlparse
import desktop_browser as browser


def stamp():
    return datetime.now(timezone.utc).isoformat()


def key(url):
    parsed=urlparse(url)
    return (parsed.scheme,parsed.netloc,parsed.path.rstrip('/'),parsed.fragment,
            tuple(sorted((k,tuple(v)) for k,v in parse_qs(parsed.query).items())))


def scope(url):
    p=urlparse(browser.safe_url(url))
    return {'origin':p.scheme+'://'+p.netloc,
            'property':parse_qs(p.query).get('resource_id',[None])[0],
            'search_console':p.hostname=='search.google.com' and p.path.startswith('/search-console')}


def same_scope(url,base):
    try:
        current=scope(url)
        return (current['origin']==base['origin'] and
                (not base['search_console'] or current['search_console'] and
                 current['property']==base['property']))
    except browser.BrowserError:
        return False


def clean_label(label,url=''):
    value=re.sub(r'^(?:home|lightbulb_2|trending_up|search|file_copy|account_tree|visibility_off|speed|link_2|trophy|settings|feedback|info)\s+','',label or '').strip()
    value=re.sub(r'^(?:flag|security)(?=[A-Z])','',value)
    return value or urlparse(url).path.rstrip('/').split('/')[-1].replace('-',' ').title()


def classify_menu(item,base):
    url=item.get('url')
    label=clean_label(item.get('label',''),url or '')
    if not label or 'keyboard_arrow_' in label or re.fullmatch(r'[\W_]+',label):
        return None
    if not url:
        if re.search(r'tentang|about',label,re.I):
            return {'name':label,'url':None,'status':'pending_dialog','kind':'information_dialog','reason':'read_only_information_dialog'}
        if re.search(r'properti|property',label,re.I):
            return {'name':label,'url':None,'status':'selector_only','reason':'property_selector_not_a_report'}
        if re.search(r'inspeksi|inspect|search|telusur',label,re.I):
            return {'name':label,'url':None,'status':'input_required','reason':'needs_explicit_url_input'}
        if re.search(r'masukan|feedback',label,re.I):
            return {'name':label,'url':None,'status':'manual_only','reason':'feedback_submission_not_a_read_only_report'}
        return None
    if not same_scope(url,base):
        return {'name':label,'url':url,'status':'out_of_scope','reason':'different_origin_or_property'}
    p=urlparse(url)
    safe_gsc_removals=base['search_console'] and p.path=='/search-console/removals'
    if (re.search(r'logout|signout|delete|purchase|checkout|transfer|\/remove\b',p.path,re.I)
            or re.search(r'(?:action|op)=(?:delete|remove|logout|transfer)',p.query,re.I)) and not safe_gsc_removals:
        return {'name':label,'url':url,'status':'manual_only','reason':'potentially_mutating_url'}
    return {'name':label,'url':url,'status':'pending','reason':''}


def inventory(args):
    snapshot=browser.desktop({'action':'snapshot','tab_id':str(args.get('tab_id',''))})
    if not snapshot.get('ok'):
        return snapshot
    base=scope(snapshot['url'])
    if base['search_console'] and not base['property']:
        raise browser.BrowserError('select_exact_search_console_property_first')
    seen=set();menus=[]
    for item in snapshot.get('menu',[]):
        menu=classify_menu(item,base)
        if not menu:continue
        identity=key(menu['url']) if menu['url'] else ('button',menu['name'])
        if identity in seen:continue
        seen.add(identity);menus.append(menu)
    return {'ok':True,'ready':snapshot.get('ready',False),'tab_id':snapshot['tab_id'],
        'start_url':snapshot['url'],'scope':base,'menus':menus,
        'page_links':snapshot.get('main_links',[]),
        'discovered':len(menus),'navigable':sum(m['status']=='pending' for m in menus)}


def page_state(snapshot,expected,base,menu_name=None):
    if not snapshot.get('ok'):return 'tool_error'
    text=snapshot.get('main_text','')
    if re.search(r'tidak memiliki akses|you don.t have access|access denied|not verified|tidak diverifikasi',
                 snapshot.get('title','')+' '+text,re.I):return 'access_denied'
    if not same_scope(snapshot.get('url',''),base):return 'property_or_target_changed'
    actual=urlparse(snapshot['url']);wanted=urlparse(expected)
    if actual.path.rstrip('/')!=wanted.path.rstrip('/') or actual.fragment!=wanted.fragment:return 'redirected_unverified'
    if base['search_console'] and menu_name:
        heading=' '.join([snapshot.get('title',''),*snapshot.get('headings',[])]).casefold()
        aliases={'links':r'\b(?:tautan|links?|laporan link)\b'}
        route=wanted.path.rstrip('/').split('/')[-1]
        words=[w for w in re.findall(r'\w+',menu_name.casefold()) if w not in ('laporan',)]
        title_matches=bool(re.search(aliases[route],heading)) if route in aliases else all(w in heading for w in words)
        if words and not title_matches:return 'stale_content'
    if not snapshot.get('ready'):return 'load_timeout'
    if re.search(r'^(?:sign in|log in|login|masuk)$',snapshot.get('title',''),re.I):return 'login_required'
    if not text.strip():return 'empty'
    if re.search(r'tidak ada data|data .*tidak memadai|no data|not enough data|tidak ada klik',text,re.I):return 'visited_empty_data'
    return 'visited'


def prepare_output(value):
    if not isinstance(value,str) or not Path(value).is_absolute():
        raise browser.BrowserError('absolute_private_output_dir_required')
    path=Path(value).resolve()
    roots=[(Path.home()/n).resolve() for n in ('Projects','Downloads')]
    if not any(root in path.parents for root in roots):
        raise browser.BrowserError('output_must_be_inside_projects_or_downloads')
    path.mkdir(parents=True,exist_ok=False,mode=0o700)
    os.chmod(path,0o700)
    return path


def write_json(path,data):
    temp=path.with_suffix('.new.json')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    os.replace(temp,path)


def summary(manifest):
    rows=manifest['menus']
    visited=[r for r in rows if r.get('url') and r.get('status') in ('visited','visited_empty_data')]
    planned=[r for r in rows if r.get('url') and r.get('status') not in ('out_of_scope','manual_only')]
    pending=[r for r in rows if r.get('status') in ('pending','pending_dialog')]
    dialogs=[r for r in rows if r.get('kind')=='information_dialog' or r.get('reason')=='read_only_information_dialog' or r.get('status') in ('visited_dialog','dialog_unavailable')]
    return {'discovered':len(rows),'navigable':len(planned),'visited':len(visited),
            'pending':len(pending),'dialogs_read':sum(r.get('status')=='visited_dialog' for r in rows),
            'links_complete':not any(r.get('links_truncated') for r in rows),
            'main_pages_complete':bool(planned) and len(visited)==len(planned),
            'complete':bool(planned) and not pending and len(visited)==len(planned) and all(r['status']=='visited_dialog' for r in dialogs) and not any(r.get('links_truncated') for r in rows),
            'statuses':{status:sum(r.get('status')==status for r in rows)
                for status in sorted({r['status'] for r in rows})}}


def markdown(manifest):
    count=summary(manifest)
    lines=['# Audit menu browser Harmonika', '',
        'Waktu: '+manifest['started_at'], 'Properti: '+str(manifest['scope']['property']),
        f"Halaman terbaca: {count['visited']}/{count['navigable']}; total item menu: {count['discovered']}.",
        f"Dialog informasi terbaca: {count['dialogs_read']}. Status keseluruhan: {manifest.get('status','running')}.",
        'Menu ditemukan bukan bukti sudah dikunjungi. Status selain visited/visited_empty_data tercatat sebagai batas atau kegagalan.', '',
        '| Menu | Status | URL aktual |', '|---|---|---|']
    for r in manifest['menus']:
        lines.append('| '+r['name'].replace('|','/')+' | '+r['status']+' | '+str(r.get('actual_url') or r.get('url') or 'Tidak ada URL').replace('|','%7C')+' |')
    for r in manifest['menus']:
        lines+=['', '## '+r['name'], 'Status: '+r['status']]
        if r.get('reason'):lines.append('Keterangan: '+r['reason'])
        if r.get('observed_at'):lines.append('Dibaca: '+r['observed_at'])
        if r.get('page_title'):lines.append('Judul halaman: '+r['page_title'])
        if r.get('headings'):lines.append('Bagian yang terlihat: '+', '.join(r['headings']))
        if r.get('controls'):lines.append('Kontrol/fungsi teramati: '+', '.join(r['controls']))
        if r.get('main_text'):lines+=['','Isi/fungsi yang benar-benar terbaca:', '',r['main_text']]
        if r.get('links'):
            lines+=['','Tautan halaman:']+['- '+x['label']+' — '+x['url'] for x in r['links']]
    lines+=['','Audit hanya membaca halaman. Tidak mengirim form, mengubah pengaturan, menambah pengguna, meminta pengindeksan atau menghapus URL. Status indeks URL tertentu tidak disimpulkan dari daftar menu.']
    return '\n'.join(lines)+'\n'


def audit(args):
    if type(args.get('max_pages',30)) is not int or not 1<=args.get('max_pages',30)<=30:
        raise browser.BrowserError('invalid_audit_page_limit')
    initial=inventory(args)
    if not initial.get('ok'):return initial
    if not initial.get('ready'):raise browser.BrowserError('start_page_not_ready')
    output=prepare_output(args.get('output_dir'))
    began=time.monotonic();tab=initial['tab_id'];base=initial['scope'];original=initial['start_url']
    manifest={**initial,'started_at':stamp(),'status':'running','menus':initial['menus'],
        'read_only':True,'private_report':True,'max_pages':args.get('max_pages',30)}
    write_json(output/'manifest.json',manifest)
    events=output/'events.jsonl'
    last_url=original;attempts=0
    try:
        for index,row in enumerate(manifest['menus']):
            if row['status'] not in ('pending','pending_dialog'):continue
            if attempts>=args.get('max_pages',30) or time.monotonic()-began>300:break
            attempts+=1;page_began=time.monotonic()
            current=browser.exec_tab(tab,'JSON.stringify({ok:true,url:location.href})')
            if not current.get('ok') or key(current.get('url',''))!=key(last_url):
                manifest['status']='stopped_target_changed';break
            is_dialog=row['status']=='pending_dialog'
            if is_dialog:
                before=browser.desktop({'action':'snapshot','tab_id':tab})
                controls=[c for c in before.get('controls',[]) if clean_label(c.get('label',''))==row['name']]
                if not controls:
                    browser.desktop({'action':'reveal_menu','tab_id':tab,'menu_label':row['name']})
                    before=browser.desktop({'action':'snapshot','tab_id':tab})
                    controls=[c for c in before.get('controls',[]) if clean_label(c.get('label',''))==row['name']]
                if len(controls)!=1 or not before.get('ready'):
                    row.update(status='dialog_unavailable',reason='control_not_available')
                    write_json(output/'manifest.json',manifest);continue
                sent=browser.desktop({'action':'click','tab_id':tab,'view_id':before['view_id'],'ref':controls[0]['ref']})
            else:
                sent=browser.desktop({'action':'navigate','tab_id':tab,'url':row['url']})
            if not sent.get('ok'):
                row.update(status='navigation_failed',reason=sent.get('error','unknown'))
            else:
                # A navigation command returning does not mean the next document exists.
                time.sleep(.3)
                expected=last_url if is_dialog else row['url']
                read_deadline=time.monotonic()+35
                reads=0
                while True:
                    reads+=1
                    try:
                        observed=browser.desktop({'action':'snapshot','tab_id':tab})
                    except browser.BrowserError as exc:
                        observed={'ok':False,'error':str(exc),'url':row['url'] or last_url}
                    state=page_state(observed,expected,base,None if is_dialog else row['name'])
                    if state not in ('load_timeout','redirected_unverified','stale_content') or time.monotonic()>=read_deadline:break
                    # Poll read-only state; never repeat a click or submission.
                    time.sleep(.5)
                row['read_attempts']=reads
                if is_dialog and state.startswith('visited'):
                    state='visited_dialog' if observed.get('dialog_visible') else 'dialog_unavailable'
                row.update(status=state,actual_url=observed.get('url',''),
                    page_title=observed.get('title',''),headings=observed.get('headings',[]),visit_method=sent.get('method','dialog_click' if is_dialog else 'navigate'),
                    controls=observed.get('main_controls',[]),main_text=observed.get('main_text','')[:10000],
                    links=observed.get('main_links',[]),link_count=observed.get('link_count',0),links_truncated=observed.get('links_truncated',False),observed_at=stamp(),
                    document_state=observed.get('document_state'),page_ready=observed.get('ready',False))
                # Never count a stale or denied page as read evidence for the expected menu.
                if state not in ('visited','visited_empty_data','visited_dialog'):
                    row['reason']='Actual page state does not prove a successfully read menu.'
                    row.update(main_text='',controls=[],links=[],link_count=0)
                last_url=observed.get('url',row['url'])
                if state=='property_or_target_changed':manifest['status']='stopped_target_changed'
                if is_dialog and observed.get('dialog_visible'):
                    closers=[c for c in observed.get('controls',[]) if re.fullmatch(r'tutup|close|selesai|done',c.get('label','').strip(),re.I)]
                    if len(closers)==1:
                        browser.desktop({'action':'click','tab_id':tab,'view_id':observed['view_id'],'ref':closers[0]['ref']})
                    else:
                        browser.desktop({'action':'navigate','tab_id':tab,'url':last_url})
                        time.sleep(.3)
            row['latency_ms']=round((time.monotonic()-page_began)*1000)
            write_json(output/'manifest.json',manifest)
            fd=os.open(events,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
            with os.fdopen(fd,'w') as f:f.write(json.dumps({'menu_index':index,'status':row['status'],'latency_ms':row['latency_ms']})+'\n')
            if manifest['status']=='stopped_target_changed':break
    except Exception as exc:
        manifest['status']='stopped_adapter_error'
        manifest['error_type']=type(exc).__name__
        if isinstance(exc,browser.BrowserError):manifest['error_code']=str(exc)
    finally:
        manifest['restore_sent']=False
        try:
            current=browser.exec_tab(tab,'JSON.stringify({ok:true,url:location.href})')
            if current.get('ok') and key(current.get('url',''))==key(last_url) and same_scope(current['url'],base):
                browser.desktop({'action':'navigate','tab_id':tab,'url':original})
                manifest['restore_sent']=True
        except browser.BrowserError:
            manifest['restore_error']='browser_unavailable_report_preserved'
        manifest['finished_at']=stamp();manifest['coverage']=summary(manifest)
        if manifest['status']=='running':manifest['status']='completed' if manifest['coverage']['complete'] else 'partial'
        write_json(output/'manifest.json',manifest)
        fd=os.open(output/'report.md',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(markdown(manifest))
    return {'ok':True,'status':manifest['status'],'coverage':manifest['coverage'],
        'report_path':str(output/'report.md'),'manifest_path':str(output/'manifest.json'),
        'menus':[{'name':r['name'],'status':r['status'],'url':r.get('actual_url') or r.get('url'),
                  'observed_functions':r.get('controls',[])[:8]} for r in manifest['menus']]}
