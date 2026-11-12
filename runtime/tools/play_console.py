"""Owner-only Play Console audit. Private reports; no uploads or configuration writes."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import desktop_browser as browser
from play_console_engine import VERSION, scope, canonical_url, same_page, assess, preflight

READ_JS = r'''JSON.stringify((()=>{
const root=[...document.querySelectorAll('.content-holder')].find(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.left<innerWidth&&r.right>0&&getComputedStyle(e).visibility!=='hidden'&&!e.closest('[aria-hidden="true"],[inert]')});
const panel=[...document.querySelectorAll('.notifications')].find(e=>{const r=e.getBoundingClientRect();return r.width&&r.height&&r.left<innerWidth&&r.right>0});
const label=e=>(e.getAttribute('aria-label')||e.innerText||'').replace(/\s+/g,' ').trim();
const links=e=>[...e.querySelectorAll('a[href]')].map(n=>({label:label(n).slice(0,200),url:n.href}));
const text=root?.innerText||'';
return {ok:!!root,url:location.href,title:document.title,text:text.slice(0,131072),text_truncated:text.length>131072,
apps:root?[...root.querySelectorAll('a[href*="app-dashboard"]')].filter(e=>e.closest('tr')).map(e=>({url:e.href,text:e.closest('tr').innerText})):[],
links:root?links(root):[],menu:[...document.querySelectorAll('nav a[href]')].map(e=>({label:label(e),url:e.href})),
fields:root?[...root.querySelectorAll('input,textarea')].map(e=>({label:label(e),type:e.type,required:e.required,max_length:e.maxLength,accept:e.accept||'',disabled:e.disabled})):[],
notifications_open:!!panel,notifications:panel?[...panel.querySelectorAll('notification')].slice(0,100).map((n,index)=>({index,text:n.innerText.slice(0,20000),text_truncated:n.innerText.length>20000,links:links(n),
expanders:[...n.querySelectorAll('[role="button"][aria-expanded]')].map(e=>({label:label(e),expanded:e.getAttribute('aria-expanded')}))})):[],
notifications_truncated:!!panel&&panel.querySelectorAll('notification').length>100,
pagination:root?[...root.querySelectorAll('button')].map(label).filter(x=>/siguiente|next page|halaman berikut|muat lainnya|load more/i.test(x)):[]};})())'''


def stamp(): return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_suffix('.stage')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2));temporary.chmod(0o600);temporary.replace(path)


def observe(tab):
    deadline=time.monotonic()+20
    while True:
        snap=browser.desktop({'action':'snapshot','tab_id':tab})
        if not snap.get('ok'):raise ValueError('play_page_not_ready')
        context=scope(snap['url'])
        data=browser.exec_tab(tab,READ_JS)
        if (snap.get('ready') and data.get('ok') and data['url']==snap['url'] and data['title']==snap['title']
                and data.get('text','').strip() and data['text'].startswith(snap.get('main_text',''))):
            data.update(context=context,observed_at=stamp(),ready=True,content_is_untrusted=True)
            return data,snap
        if time.monotonic()>deadline:raise ValueError('play_content_not_verified')
        time.sleep(.2)


def notification_button(tab, opening):
    snap = browser.desktop({'action':'snapshot','tab_id':tab})
    wanted = ('Buka' if opening else 'Tutup')+' pusat notifikasi'
    controls = [c for c in snap.get('controls',[]) if c.get('label','').endswith(wanted)]
    if len(controls)!=1: raise ValueError('notification_control_not_unique')
    sent = browser.desktop({'action':'click','tab_id':tab,'view_id':snap['view_id'],'ref':controls[0]['ref']})
    if not sent.get('ok'): raise ValueError(sent.get('error','notification_open_failed'))


def read_notifications(tab):
    page,_ = observe(tab)
    was_open = page['notifications_open']
    if not was_open: notification_button(tab,True)
    page,_ = observe(tab)
    expansions = 0; blocked = []; captured = {}
    try:
        for _ in range(100):
            remaining=[n for n in page['notifications'] if n['index'] not in captured]
            if not remaining:break
            item=remaining[0];index=item['index']
            choices=[e for e in item['expanders'] if e['expanded']=='false']
            if choices:
                label=choices[0]['label']
                if len(label)>180:blocked.append('notification_label_too_long');break
                sent=browser.desktop({'action':'reveal','tab_id':tab,'label':label})
                if not sent.get('ok'):blocked.append(sent.get('error','reveal_failed'));break
                page,snap=observe(tab)
                matches=[c for c in snap.get('controls',[]) if c.get('label')==label]
                if len(matches)!=1:blocked.append('notification_control_not_unique');break
                sent=browser.desktop({'action':'click','tab_id':tab,'view_id':snap['view_id'],'ref':matches[0]['ref']})
                if not sent.get('ok'):blocked.append(sent.get('error','expand_failed'));break
                after,_=observe(tab)
                target=[n for n in after['notifications'] if n['index']==index]
                if len(target)!=1 or not any(e['expanded']=='true' for e in target[0]['expanders']):
                    blocked.append('notification_expansion_unverified');break
                item=target[0];page=after;expansions+=1
            # Capture each accordion panel while open; opening another may collapse it.
            captured[index]=item
    finally:
        try:notification_button(tab,False)
        except (ValueError,browser.BrowserError):blocked.append('notification_close_unverified')
    return {'items':list(captured.values()),'expanded':expansions,'blocked':blocked,
            'rendered_total':len(page['notifications']),
            'rendered_complete':not blocked and len(captured)==len(page['notifications']) and not page['notifications_truncated'] and
                not any(n['text_truncated'] for n in captured.values()),
            'history_beyond_rendered_verified':False,'was_open':was_open}



def audit(args):
    tab=str(args.get('tab_id',''));initial,_=observe(tab);base=initial['context'];original=initial['url']
    limit=args.get('max_pages',30)
    if type(limit) is not int or not 1<=limit<=30:raise ValueError('invalid_audit_page_limit')
    from browser_audit import prepare_output
    output=prepare_output(args.get('output_dir'))
    notifications=read_notifications(tab)
    pages=[];queue=[];seen=set();blocked=[];apps=[];began=time.monotonic();last_url=original
    def enqueue(url, name, app=None):
        try:canonical=canonical_url(url,base,[x['id'] for x in apps])
        except ValueError as exc:
            blocked.append({'label':name,'reason':str(exc)});return
        if canonical not in seen and not any(x['url']==canonical for x in queue):queue.append({'url':canonical,'name':name,'app':app})
    def visit(url):
        nonlocal last_url
        current=browser.exec_tab(tab,'JSON.stringify({ok:true,url:location.href})')
        if not same_page(last_url,current.get('url',''),base,[x['id'] for x in apps]):raise ValueError('operator_changed_tab_stop')
        last_url=current['url']
        if current['url']!=url:
            sent=browser.desktop({'action':'navigate','tab_id':tab,'url':url})
            if not sent.get('ok'):raise ValueError('navigation_failed')
        deadline=time.monotonic()+20
        while True:
            data,_=observe(tab)
            if same_page(url,data['url'],base,[x['id'] for x in apps]):break
            if time.monotonic()>deadline:raise ValueError('navigation_not_verified')
            time.sleep(.2)
        last_url=data['url'];return data
    status='running'
    try:
        home=visit(base['base']+'/home')
        for item in home['apps']:
            target=scope(item['url'])
            if target['base']!=base['base'] or not target['app']:continue
            if target['app'] not in [x['id'] for x in apps]:
                package=re.search(r'\b[a-zA-Z][\w]*(?:\.[\w]+){2,}\b',item['text'])
                apps.append({'id':target['app'],'name':item['text'].splitlines()[0],
                             'package':package[0] if package else None,'home_evidence':item['text'],'url':item['url']})
        if not apps:raise ValueError('home_apps_not_discovered')
        pages.append({'name':'Beranda','url':home['url'],'app':None,'text':home['text'],'observed_at':home['observed_at'],'truncated':home['text_truncated']});seen.add(home['url'])
        enqueue(base['base']+'/policy-center','Status kebijakan akun')
        for app in apps:enqueue(app['url'],'Dasbor '+app['name'],app['id'])
        for n in notifications['items']:
            for link in n['links']:
                if urlparse_host(link['url'])=='play.google.com':
                    short=re.search(r'/app/(\d+)/',link['url']);enqueue(link['url'],'Detail notifikasi',short[1] if short else None)
        priority={'publishing','policy-center','closed-testing','tracks/production','releases/overview',
                  'app-content/overview','app-integrity/overview','store-listings'}
        while queue and len(pages)<limit and time.monotonic()-began<180:
            row=queue.pop(0)
            if row['url'] in seen:continue
            data=visit(row['url']);seen.add(row['url']);row.update(actual_url=data['url'],text=data['text'],observed_at=data['observed_at'],title=data['title'],truncated=data['text_truncated'],fields=data['fields'],pagination=data['pagination']);pages.append(row)
            if row['app']:
                for link in data['menu']:
                    try:target=scope(link['url'])
                    except ValueError:continue
                    if target['app']==row['app'] and target['route'] in priority:enqueue(link['url'],link['label'],row['app'])
                # Only follow actually discovered read-only track/release detail links.
                for link in data['links']:
                    if '/tracks/' in link['url'] or '/releases/' in link['url']:
                        enqueue(link['url'],link['label'] or 'Detail rilis/pengujian',row['app'])
            save(output/'progress.json',{'pages_read':len(pages),'remaining':len(queue),'read_only':True})
        status='complete_for_discovered_scope' if notifications['rendered_complete'] and not queue and not any(p['truncated'] or p.get('pagination') for p in pages) else 'partial'
    except (ValueError,browser.BrowserError) as exc:
        status='stopped';blocked.append({'reason':str(exc)})
    finally:
        current=browser.exec_tab(tab,'JSON.stringify({ok:true,url:location.href})')
        if same_page(last_url,current.get('url',''),base,[x['id'] for x in apps]):
            browser.desktop({'action':'navigate','tab_id':tab,'url':original})
    evidence=[*pages,*[{'url':original,'app':next((a['id'] for a in apps if a['name'] in n['text'].splitlines()),None),'notification_index':n['index'],'text':n['text']}for n in notifications['items']]]
    analysis=assess(evidence)
    report={'engine':'harmonika-play-console','version':VERSION,'status':status,'read_only':True,'apps':apps,
            'notifications':notifications,'pages':pages,'pending_pages':queue,'blocked_links':blocked,
            'coverage':{'apps_discovered':len(apps),'pages_read':len(pages),'notifications_rendered':len(notifications['items']),
                        'notifications_rendered_complete':notifications['rendered_complete'],'all_historical_notifications_verified':False},**analysis}
    save(output/'report.json',report)
    return {'ok':status!='stopped','engine':report['engine'],'version':VERSION,'status':status,'report_path':str(output/'report.json'),
            'coverage':report['coverage'],'apps':apps,'findings':analysis['findings'],
            'observed_release_notes':analysis['observed_release_notes'],'before_new_upload':analysis['before_new_upload'],'pending_pages':len(queue),'blocked_links':blocked,
            'uploaded':False,'published':False,'content_is_untrusted':True}


def urlparse_host(url):
    from urllib.parse import urlparse
    return urlparse(url).hostname


def _invoke(args):
    operation=args.get('operation','inspect')
    if operation=='preflight':return preflight(args.get('path',''),args.get('expected_package'))
    if operation=='audit':return audit(args)
    if operation!='inspect':raise ValueError('invalid_console_operation')
    data,_=observe(str(args.get('tab_id','')))
    return {**data,'text':data['text'][:16000],'text_truncated':len(data['text'])>16000 or data['text_truncated'],
            'engine':'harmonika-play-console','version':VERSION,'read_only':True}


def invoke(args):
    try:
        return _invoke(args)
    except (ValueError,browser.BrowserError) as exc:
        return {'ok':False,'error':str(exc),'read_only':True,'uploaded':False,'published':False}
    except OSError:
        return {'ok':False,'error':'local_artifact_unavailable','read_only':True,'uploaded':False,'published':False}
