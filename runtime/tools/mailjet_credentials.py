"""Owner-local Mailjet credential capture. Secrets never enter a tool result."""
import json
import os
from pathlib import Path
import re
import tempfile
import time
import subprocess
from urllib.parse import urlparse

ROOT=Path.home()/'.config/harmonika-terminal'
HOST='app.mailjet.com'
VIEWS={}

GUARD="if(location.protocol!=='https:'||location.hostname!=='app.mailjet.com')return JSON.stringify({ok:false,error:'mailjet_tab_scope_changed'});"
ROOTS=r'''const roots=[document],seen=new Set();for(let i=0;i<roots.length&&i<256;i++){const root=roots[i];if(seen.has(root))continue;seen.add(root);for(const e of root.querySelectorAll('*')){if(e.shadowRoot&&!seen.has(e.shadowRoot))roots.push(e.shadowRoot);if(e.tagName==='IFRAME')try{if(e.contentWindow.location.origin===location.origin&&e.contentDocument&&!seen.has(e.contentDocument))roots.push(e.contentDocument)}catch{}}}const elements=[...new Set(roots.slice(0,256).flatMap(r=>[...r.querySelectorAll('*')]))].slice(0,20000);'''
INSPECT=r'''(()=>{__GUARD__
__ROOTS__
const scrub=s=>String(s||'').replace(/\b[a-f0-9]{32,128}\b/ig,'[credential]').replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/ig,'[email]').slice(0,120);
return JSON.stringify({ok:true,path:location.pathname,links:[...document.querySelectorAll('a[href]')].map(e=>({label:scrub(e.innerText||e.getAttribute('aria-label')),path:(()=>{try{const u=new URL(e.href);return u.origin===location.origin?u.pathname:null}catch{return null}})()})).filter(e=>e.path&&!/[a-f0-9]{32,128}/i.test(e.path)&&/smtp|api.?key|settings/i.test(e.path+' '+e.label)).slice(0,50),buttons:[...document.querySelectorAll('button')].map(e=>scrub(e.innerText||e.getAttribute('aria-label'))).filter(e=>/smtp|api|key|secret|show|reveal|copy|settings/i.test(e)).slice(0,30),credential_fields:[...document.querySelectorAll('input,textarea')].map(e=>({type:e.type,label:scrub(e.getAttribute('aria-label')||e.name||e.id),value_present:!!e.value})).slice(0,30)});
})()'''.replace('__GUARD__',GUARD).replace('__ROOTS__',ROOTS).replace("[...document.querySelectorAll('a[href]')]","elements.filter(e=>e.matches('a[href]'))").replace("[...document.querySelectorAll('button')]","elements.filter(e=>e.matches('button,[role=button],sinch-button,sinch-action-menu-option'))").replace("[...document.querySelectorAll('input,textarea')]","elements.filter(e=>e.matches('input,textarea'))")
CAPTURE=r'''(()=>{__GUARD__
__ROOTS__
const values=[];const add=(value,label,kind)=>{value=String(value||'').trim();if(/^[a-f0-9]{32,128}$/i.test(value))values.push({value,label:String(label||'').slice(0,250),kind})};
for(const e of elements)if(e.matches('input,textarea'))add(e.value,[...[...(e.labels||[])].map(l=>l.innerText),e.getAttribute('aria-label'),e.name,e.id,e.placeholder,e.parentElement?.innerText].join(' '),e.type);
for(const e of elements)if(e.children.length===0&&e.tagName!=='SCRIPT'&&e.tagName!=='STYLE')add(e.textContent,[e.getAttribute('aria-label'),e.parentElement?.innerText,e.getRootNode()?.host?.textContent].join(' '),e.tagName);
for(const e of elements)if(e.tagName!=='SCRIPT'&&e.tagName!=='STYLE')for(const n of e.childNodes)if(n.nodeType===3)add(n.nodeValue,[e.getAttribute('aria-label'),e.parentElement?.innerText,e.getRootNode()?.host?.textContent].join(' '),e.tagName);
return JSON.stringify({ok:true,path:location.pathname,candidates:values.slice(0,80)});
})()'''.replace('__GUARD__',GUARD).replace('__ROOTS__',ROOTS)

AUDIT=r'''(()=>{__GUARD____ROOTS__
const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&getComputedStyle(e).display!=='none';
const scrub=s=>String(s||'').replace(/\b[a-f0-9]{20,128}\b/ig,'[redacted]').replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/ig,'[email]').slice(0,450);
const account_flags={};
// Parse only this page's existing bootstrap JSON locally. Never return it.
for(const e of elements.filter(e=>e.tagName==='SCRIPT')){
const text=e.textContent||'';if(text.length>250000||!text.includes('PageApiKeyManagement'))continue;
const match=text.match(/components\[[^\]]+\]\s*=\s*(\{[\s\S]*\})\s*;?\s*$/);if(!match)continue;
try{const c=JSON.parse(match[1]);const user=c?.context?.data?.user;if(c.name!=='PageApiKeyManagement'||!user)continue;
for(const k of ['requiresEmailVerification','requiresBusinessVerification','isMaster','isBasicOrFreePlan','canCreateApiKey','apikeysLimit'])if(['boolean','number'].includes(typeof user[k]))account_flags[k]=user[k];}catch{}}
const notices=[...new Set(elements.filter(e=>!['SCRIPT','STYLE'].includes(e.tagName)&&e.children.length===0&&visible(e)&&/verif|suspend|activat|permission|restricted|limit|confirm your|unable|could not|error|not generated/i.test(e.textContent||'')).map(e=>scrub(e.textContent)))].slice(0,25);
const links=elements.filter(e=>e.matches('a[href]')&&/verif|account|profile|activat|support/i.test((e.innerText||'')+' '+e.getAttribute('href'))).map(e=>({label:scrub(e.innerText),path:(()=>{try{const u=new URL(e.href);return u.origin===location.origin&&!/token|password|key|session/i.test(u.search)&&!/[a-f0-9]{20,128}/i.test(u.pathname)?u.pathname:null}catch{return null}})()})).filter(e=>e.path).slice(0,25);
return JSON.stringify({ok:true,path:location.pathname,account_flags,notices,links,secret_generation_attempted:false});})()'''.replace('__GUARD__',GUARD).replace('__ROOTS__',ROOTS)


def select_pair(candidates,expected=None):
    if not isinstance(candidates,list)or len(candidates)>80:raise ValueError('invalid_capture')
    keys={};secrets={}
    all_values=set()
    for row in candidates:
        value=row.get('value','')
        if not isinstance(value,str)or not re.fullmatch('[a-fA-F0-9]{32,128}',value):continue
        all_values.add(value)
        label=str(row.get('label','')).lower()
        if row.get('kind')=='password'or re.search(r'secret|private|rahasia',label):secrets[value]=True
        elif re.search(r'api.?key|public|username',label):keys[value]=True
    if expected:
        if expected not in all_values:raise ValueError('expected_api_key_not_on_page')
        key=expected
    elif len(keys)==1:key=next(iter(keys))
    else:raise ValueError('api_key_ambiguous_or_not_visible')
    possible=set(secrets)-{key}
    if len(possible)!=1:raise ValueError('secret_key_ambiguous_or_not_visible')
    return key,possible.pop()


def write_private(path,data):
    fd,temporary=tempfile.mkstemp(prefix='.mailjet-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w')as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def native_control(tab,label,browser):
    """One physical click, scoped to an observed, visible Mailjet control.

    Some Nectary controls emit their semantic event only from native pointer
    input. The DOM guard and hit test are mandatory before an OS click.
    """
    focus='''function run(argv){const a=Application('Google Chrome');for(const w of a.windows()){const tabs=w.tabs();for(let i=0;i<tabs.length;i++)if(String(tabs[i].id())===argv[0]){if(!/^https:\\/\\/app\\.mailjet\\.com\\//.test(tabs[i].url()))return JSON.stringify({ok:false});w.activeTabIndex=i+1;w.index=1;a.activate();return JSON.stringify({ok:true});}}return JSON.stringify({ok:false});}'''
    # run_jxa normally takes JSON payload; a fixed wrapper decodes it here.
    focus=focus.replace('argv[0]','JSON.parse(argv[0]).tab')
    focused=browser.run_jxa(focus,{'tab':tab})
    if not focused.get('ok'):return {'ok':False,'error':'mailjet_native_focus_failed','action_sent':False}
    time.sleep(.3)
    code="(()=>{"+GUARD+ROOTS+"const label="+json.dumps(label)+r''';const matches=elements.filter(e=>e.tagName==='SINCH-BUTTON'&&(e.getAttribute('aria-label')||'').trim()===label);if(matches.length!==1)return JSON.stringify({ok:false,error:'native_control_ambiguous'});const e=matches[0];e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();if(!r.width||!r.height||r.x<0||r.y<0||r.right>innerWidth||r.bottom>innerHeight)return JSON.stringify({ok:false,error:'native_control_not_visible'});const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);if(hit!==e&&!e.contains(hit))return JSON.stringify({ok:false,error:'native_control_obscured'});return JSON.stringify({ok:true,x:Math.round(screenX+r.x+r.width/2),y:Math.round(screenY+outerHeight-innerHeight+r.y+r.height/2)});})()'''
    point=browser.exec_tab(tab,code)
    if not point.get('ok'):return point
    if any(type(point.get(k))is not int or not 0<=point[k]<20000 for k in ('x','y')):
        return {'ok':False,'error':'native_control_coordinates_invalid','action_sent':False}
    try:
        response=subprocess.run(['/usr/bin/osascript','-e','tell application "System Events" to click at {'+str(point['x'])+','+str(point['y'])+'}'],capture_output=True,timeout=10)
    except subprocess.TimeoutExpired:return {'ok':False,'error':'native_click_outcome_unknown','action_sent':True}
    return {'ok':response.returncode==0,'native_click_dispatched':response.returncode==0,'verification_required':True}


def invoke(args):
    allowed={'action','tab_id','path','authorized_secret_capture','authorized_secret_generation'}
    if not isinstance(args,dict)or set(args)-allowed:return {'ok':False,'error':'invalid_mailjet_request'}
    action=args.get('action','inspect');tab=args.get('tab_id')
    if isinstance(tab,int)and not isinstance(tab,bool):tab=str(tab)
    matched=re.fullmatch(r'(?:tab[-_: ]*)?(\d{1,15})',tab,re.I)if isinstance(tab,str)else None
    if not matched:return {'ok':False,'error':'invalid_tab_id'}
    tab=matched[1]
    import desktop_browser as browser
    if action=='audit_account':
        result=browser.exec_tab(tab,AUDIT)
        if result.get('ok'):
            from zimbra_relay import assess_account
            result['assessment']=assess_account(result.get('account_flags',{}))
        if result.get('ok'):VIEWS[tab]={r['path']for r in result.get('links',[])if r.get('path')}
        return result
    if action=='inspect':
        result=browser.exec_tab(tab,INSPECT)
        if result.get('ok'):VIEWS[tab]={r['path']for r in result.get('links',[])if r.get('path')}
        return result
    if action=='navigate_settings':
        path=args.get('path')
        if path not in VIEWS.get(tab,set())or re.search(r'reset|regenerate|create|delete|revoke',path,re.I):
            return {'ok':False,'error':'observe_mailjet_settings_link_first'}
        code="(()=>{"+GUARD+"const path="+json.dumps(path)+";location.href=location.origin+path;return JSON.stringify({ok:true,navigation_dispatched:true,path});})()"
        return browser.exec_tab(tab,code)
    if action=='reveal_api_key':
        # Reading an existing API key is authorized here; generation/rotation is
        # deliberately a different account-changing operation and unsupported.
        code="(()=>{"+GUARD+ROOTS+r'''const label=e=>(e.getAttribute('aria-label')||e.innerText||e.textContent||'').trim();let items=elements.filter(e=>e.tagName==='BUTTON'&&/^show api key$/i.test(label(e))&&!e.disabled);if(!items.length)items=elements.filter(e=>e.tagName==='SINCH-BUTTON'&&/^show api key$/i.test(label(e)));if(items.length!==1)return JSON.stringify({ok:false,error:'api_key_reveal_ambiguous'});items[0].scrollIntoView({block:'center'});let target=items[0];const rect=target.getBoundingClientRect();for(let depth=0;depth<8&&target.shadowRoot;depth++){const next=target.shadowRoot.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);if(!next||next===target)break;target=next;}target.click();return JSON.stringify({ok:true,existing_api_key_reveal_dispatched:true,secret_generated:false});})()'''
        return native_control(tab,'Show API key',browser)
    if action in ('generate_secret_key','confirm_generation'):
        if args.get('authorized_secret_generation')is not True:
            return {'ok':False,'error':'explicit_generation_permission_required'}
        grant_path=ROOT/'mailjet-generation-grant.json'
        try:
            s=grant_path.lstat()
            if grant_path.is_symlink()or s.st_uid!=os.getuid()or s.st_mode&0o077:raise ValueError()
            grant=json.loads(grant_path.read_text())
            if grant.get('tab_id')!=tab or grant.get('operation')!='generate_mailjet_secret'or not 0<=time.time()-grant.get('at',0)<1800:
                raise ValueError()
        except (OSError,ValueError,TypeError):return {'ok':False,'error':'owner_generation_grant_required'}
        if action=='generate_secret_key':
            if grant.get('state')!='approved':return {'ok':False,'error':'generation_already_dispatched_reconcile_do_not_retry'}
            account=browser.exec_tab(tab,AUDIT)
            from zimbra_relay import assess_account
            assessment=assess_account(account.get('account_flags',{}))
            if not account.get('ok')or not assessment['key_generation_ready']:
                return {'ok':False,'error':'resolve_mailjet_account_prerequisites_first','account_assessment':assessment,'action_sent':False}
            expected=(ROOT/'mailjet-api-key').read_text().strip()
            observed=browser.exec_tab(tab,CAPTURE)
            if not observed.get('ok')or not any(r.get('value')==expected for r in observed.get('candidates',[])):
                return {'ok':False,'error':'expected_api_key_must_be_visible_before_generation'}
            code="(()=>{"+GUARD+ROOTS+r'''const label=e=>(e.getAttribute('aria-label')||e.innerText||e.textContent||'').trim();let items=elements.filter(e=>e.tagName==='BUTTON'&&/^generate secret key$/i.test(label(e))&&!e.disabled);if(!items.length)items=elements.filter(e=>e.tagName==='SINCH-BUTTON'&&/^generate secret key$/i.test(label(e)));if(items.length!==1)return JSON.stringify({ok:false,error:'generation_control_ambiguous',action_sent:false});items[0].scrollIntoView({block:'center'});let target=items[0];const rect=target.getBoundingClientRect();for(let depth=0;depth<8&&target.shadowRoot;depth++){const next=target.shadowRoot.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);if(!next||next===target)break;target=next;}target.click();return JSON.stringify({ok:true,generation_control_dispatched:true,verification_required:true});})()'''
            grant['state']='dispatched'
        else:
            if grant.get('state')!='dispatched':return {'ok':False,'error':'generation_confirmation_not_pending'}
            code="(()=>{"+GUARD+ROOTS+r'''const dialogs=elements.filter(e=>e.matches('[role=dialog],sinch-dialog')&&/secret\s+key/i.test(e.innerText||e.textContent||'')&&e.getClientRects().length);const within=e=>dialogs.some(d=>d.contains(e)||e.getRootNode()?.host===d);const label=e=>(e.getAttribute('aria-label')||e.innerText||e.textContent||'').trim();const items=elements.filter(e=>e.tagName==='BUTTON'&&within(e)&&/^(?:generate|generate secret key|confirm|regenerate|regenerate secret key)$/i.test(label(e))&&!e.disabled);if(items.length!==1)return JSON.stringify({ok:false,error:'generation_confirmation_ambiguous',action_sent:false});items[0].scrollIntoView({block:'center'});let target=items[0];const rect=target.getBoundingClientRect();for(let depth=0;depth<8&&target.shadowRoot;depth++){const next=target.shadowRoot.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);if(!next||next===target)break;target=next;}target.click();return JSON.stringify({ok:true,generation_confirmation_dispatched:true,verification_required:true});})()'''
            grant['state']='confirmation_dispatched'
        # Persist BEFORE dispatch. On an unknown result, observe instead of replay.
        write_private(grant_path,json.dumps(grant))
        if action=='generate_secret_key':return native_control(tab,'Generate Secret Key',browser)
        return browser.exec_tab(tab,code)
    if action!='capture'or args.get('authorized_secret_capture')is not True:
        return {'ok':False,'error':'explicit_mailjet_secret_capture_authorization_required'}
    result=browser.exec_tab(tab,CAPTURE)
    if not result.get('ok'):return {'ok':False,'error':'mailjet_tab_scope_changed'}
    expected_file=ROOT/'mailjet-api-key'
    # Existing API key file is owner-only, never printed or returned.
    expected=None
    if expected_file.exists():
        s=expected_file.lstat()
        if expected_file.is_symlink()or s.st_uid!=os.getuid()or s.st_mode&0o077:return {'ok':False,'error':'unsafe_api_key_reference'}
        expected=expected_file.read_text().strip()
    try:key,secret=select_pair(result.get('candidates'),expected)
    except ValueError as exc:return {'ok':False,'error':str(exc),'credentials_saved':False}
    # This is the sole point where DOM secret values leave browser memory.
    # Files are local and private. No raw DOM, candidate, key, fingerprint or
    # exception body is sent to an LLM, stdout, SQLite or remote service here.
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    write_private(ROOT/'mailjet-api-key',key+'\n')
    write_private(ROOT/'mailjet-secret-key',secret+'\n')
    write_private(ROOT/'mailjet-credentials.json',json.dumps({'api_key':key,'secret_key':secret})+'\n')
    grant_path=ROOT/'mailjet-generation-grant.json'
    if grant_path.exists():
        try:
            grant=json.loads(grant_path.read_text());grant['state']='captured';write_private(grant_path,json.dumps(grant))
        except (OSError,ValueError):pass
    return {'ok':True,'credentials_saved':True,'api_key_present':True,'secret_key_present':True,
            'private_reference':'owner-local:mailjet','mode':'0600','values_returned':False}


TOOLS=[{'name':'mailjet_credentials','description':'Owner-local authorized Mailjet Chrome helper. inspect returns sanitized links/field metadata only, never DOM text or key values. navigate_settings only follows observed same-origin links without credential mutations. capture requires existing explicit user permission and authorized_secret_capture=true, reads existing Mailjet key fields in the selected app.mailjet.com tab and saves keys directly to owner-only local references; never returns credentials. Generation requires a separate explicit owner grant and account prerequisite checks. No automatic reset, cookies, unrelated tabs, screenshots, login or sending.',
        'inputSchema':{'type':'object','properties':{'action':{'type':'string','enum':['inspect','audit_account','navigate_settings','reveal_api_key','capture','generate_secret_key','confirm_generation']},'tab_id':{'type':'string'},'path':{'type':'string'},'authorized_secret_capture':{'type':'boolean','default':False},'authorized_secret_generation':{'type':'boolean','default':False}},'required':['action','tab_id'],'additionalProperties':False}}]
HANDLERS={'mailjet_credentials':invoke}
