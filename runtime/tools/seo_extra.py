"""Measured adapters: PSI, link verification, drift and owner-authorized Google data."""
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qsl
from html.parser import HTMLParser


def response(status='completed',**fields):
    return dict(schema_version=1,status=status,content_is_untrusted=True,**fields)

def unavailable(reason):
    return response('unavailable',reason=reason,limitations=['No measurement or private account data is fabricated'])

def checked_json(result):
    if result.get('status')!=200:raise ValueError('provider_http_error')
    value=json.loads(result['text'])
    if not isinstance(value,dict):raise ValueError('provider_invalid_json')
    return value

class SnapshotParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.title=[];self.heading=[];self.current=None;self.meta={};self.canonical=[];self.scripts=[]
    def handle_starttag(self,tag,attrs):
        attrs={key:(value or '') for key,value in attrs}
        if tag=='title' or tag in ('h1','h2'):self.current=tag
        if tag=='meta':self.meta[(attrs.get('name') or attrs.get('property') or '').lower()]=attrs.get('content','')[:2048]
        if tag=='link' and 'canonical' in attrs.get('rel','').lower().split():self.canonical.append(attrs.get('href','')[:2048])
    def handle_endtag(self,tag):
        if tag==self.current:self.current=None
    def handle_data(self,text):
        if self.current=='title':self.title.append(text[:2048])
        elif self.current in ('h1','h2'):self.heading.append(text[:2048])

def snapshot(page):
    parser=SnapshotParser();parser.feed(page['text'])
    # Fields and hashes only; never persist the entire retrieved HTML.
    value={'url':page['url'],'status_code':page['status'],'title':' '.join(parser.title)[:2048],
           'description':parser.meta.get('description',''),'robots':parser.meta.get('robots',''),
           'canonical':parser.canonical[:5],'headings':parser.heading[:30]}
    value['content_sha256']=hashlib.sha256(page['text'].encode()).hexdigest()
    return value

def atomic_json(path,value):
    if not path.parent.is_dir():raise ValueError('output_parent_missing')
    pending=None
    try:
        with tempfile.NamedTemporaryFile(prefix='harmonika-seo-',suffix='.pending',dir=str(path.parent),delete=False) as out:
            pending=Path(out.name);out.write(json.dumps(value,ensure_ascii=False).encode());out.flush();os.fsync(out.fileno())
        os.link(pending,path)
    finally:
        if pending:pending.unlink(missing_ok=True)

def drift(payload,fetcher):
    page=fetcher(payload['url']);current=snapshot(page);file=Path(payload['baseline_path'])
    if payload['action']=='capture':
        if page['status']!=200:return response('degraded',reason='baseline_requires_http_200',http_status=page['status'])
        atomic_json(file,{'schema_version':1,'captured_at':datetime.now(timezone.utc).isoformat(),'snapshot':current})
        return response(baseline_path=str(file),snapshot=current,method='captured_http_snapshot')
    if file.stat().st_size>262144:raise ValueError('baseline_too_large')
    baseline=json.loads(file.read_text())
    previous=baseline.get('snapshot') if isinstance(baseline,dict) else None
    if not isinstance(previous,dict) or previous.get('url')!=current['url']:return response('degraded',reason='baseline_url_mismatch')
    findings=[{'code':'changed_'+key,'severity':'high' if key in ('status_code','robots','canonical') else 'info','before':previous.get(key),'after':value} for key,value in current.items() if key!='url' and previous.get(key)!=value]
    return response(snapshot=current,findings=findings,baseline_path=str(file),method='snapshot_comparison',limitations=['HTML hash changes can include dynamic markup; not proof of ranking loss'])

def performance(payload,fetcher,principal=None):
    from seo_network import validate_url
    target=validate_url(payload['url'])
    if any(k.lower() in {'token','access_token','refresh_token','password','api_key','secret','client_secret','session','sessionid','authorization'} for k,v in parse_qsl(urlsplit(target).query)):
        return response('degraded',reason='sensitive_url_not_sent_to_provider',measured=False)
    parameters={'url':target,'strategy':payload.get('strategy','mobile'),'category':'performance'}
    if principal:
        settings,reason=google_settings(principal)
        key=settings.get('pagespeed_api_key') if settings else None
        if not reason and isinstance(key,str) and re.fullmatch('[A-Za-z0-9_-]{1,512}',key):parameters['key']=key
    url='https://www.googleapis.com/pagespeedonline/v5/runPagespeed?'+urlencode(parameters)
    try:
        raw=fetcher.fetch(url)
        if raw.get('status')!=200:return response('degraded',reason='performance_rate_limited' if raw.get('status')==429 else 'performance_provider_http_error',http_status=raw.get('status'),measured=False)
        data=checked_json(raw)
        lh=data.get('lighthouseResult',{});score=lh.get('categories',{}).get('performance',{}).get('score')
        if not isinstance(score,(int,float)) or isinstance(score,bool) or not 0<=score<=1:return response('degraded',reason='performance_measurement_missing')
        names=('largest-contentful-paint','cumulative-layout-shift','total-blocking-time','first-contentful-paint','speed-index')
        audits=lh.get('audits',{})
        metrics={k:{'value':audits[k].get('numericValue'),'unit':audits[k].get('numericUnit'),'display':audits[k].get('displayValue')} for k in names if isinstance(audits.get(k),dict)}
        field=data.get('loadingExperience',{}).get('metrics',{})
        return response(method='google_pagespeed_insights',strategy=payload.get('strategy','mobile'),lab_score=round(score*100),lab_metrics=metrics,
                        field_metrics=field,field_data_available=bool(field),lighthouse_version=lh.get('lighthouseVersion'),fetched_at=lh.get('fetchTime'),
                        limitations=['Lighthouse is lab data; field INP and Core Web Vitals require actual field observations; TBT is not INP'])
    except Exception as error:return response('degraded',reason=getattr(error,'code','performance_provider_unavailable'),method='google_pagespeed_insights',measured=False)

class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a' and len(self.links)<5000:self.links.append(dict(attrs))

def backlinks(payload,fetcher):
    target=urlsplit(payload['url']).hostname;results=[]
    if not payload.get('links'):return response('degraded',reason='backlink_sources_required',method='provided_link_verification',limitations=['No discovery, authority score or live backlink inventory without a data source'])
    for url in payload['links']:
        try:
            page=fetcher(url);parser=Links();parser.feed(page['text'])
            matches=[]
            for anchor in parser.links:
                href=anchor.get('href')
                if not isinstance(href,str) or not href.strip() or len(href)>2048:continue
                try:
                    candidate=urljoin(page['url'],href)
                    host=urlsplit(candidate).hostname
                    if host and host.encode('idna').decode().lower()==target.encode('idna').decode().lower():matches.append({'target':candidate,'rel':(anchor.get('rel') or '')[:256]})
                except (ValueError,UnicodeError):continue
            results.append({'source':page['url'],'http_status':page['status'],'verified':bool(matches) if page['status']==200 else None,'source_scope':'same_site' if urlsplit(page['url']).hostname==target else 'external','links':matches[:10]})
        except Exception as error:results.append({'source':url,'verified':None,'reason':getattr(error,'code','fetch_failed')})
    return response('degraded' if any(r['verified'] is None for r in results) else 'completed',method='provided_link_verification',results=results,
                    limitations=['Only supplied pages checked; not a complete backlink profile or authority/ranking score'])

def google_settings(principal):
    raw=os.environ.get('HARMONIKA_SEO_GOOGLE_CONFIG','')
    if not raw:return None,'google_credentials_not_configured'
    file=Path(raw)
    try:
        if not file.is_absolute() or file.is_symlink() or file.stat().st_size>65536 or file.stat().st_mode&0o077 or file.stat().st_uid!=os.getuid():return None,'google_config_permissions_invalid'
        data=json.loads(file.read_text())
        if not isinstance(data,dict):return None,'google_config_invalid'
        actors=data.get('principal_ids',[])
        if not isinstance(actors,list) or len(actors)>32 or any(not isinstance(p,str) for p in actors):return None,'google_config_invalid'
        if principal not in actors:return None,'google_principal_not_authorized'
        for key in ('search_console_properties','ga4_properties'):
            props=data.get(key,[])
            if not isinstance(props,list) or len(props)>100 or any(not isinstance(p,str) for p in props):return None,'google_config_invalid'
        return data,None
    except Exception:return None,'google_config_invalid'

def google_config(principal):
    data,reason=google_settings(principal)
    if reason:return data,reason
    if not isinstance(data.get('access_token'),str) and not all(isinstance(data.get(k),str) and data[k] for k in ('refresh_token','client_id','client_secret')):
        return None,'google_authorization_required'
    return data,None


def token(config,fetcher):
    expiry=config.get('expires_at',0)
    if isinstance(config.get('access_token'),str) and isinstance(expiry,(int,float)) and expiry>time.time()+30:return config['access_token']
    if not all(config.get(k) for k in ('refresh_token','client_id','client_secret')):raise ValueError('google_authorization_expired')
    body=urlencode({k:config[k] for k in ('refresh_token','client_id','client_secret')}|{'grant_type':'refresh_token'}).encode()
    data=checked_json(fetcher.fetch('https://oauth2.googleapis.com/token',method='POST',headers={'Content-Type':'application/x-www-form-urlencoded'},body=body))
    if not isinstance(data.get('access_token'),str):raise ValueError('google_authorization_failed')
    return data['access_token']

def google(operation,payload,fetcher,principal):
    config,reason=google_config(principal)
    if reason:return unavailable(reason)
    try:
        start=date.fromisoformat(payload['start_date']);end=date.fromisoformat(payload['end_date'])
        if start>end or (end-start).days>366:raise ValueError()
    except (ValueError,TypeError):return response('degraded',reason='invalid_date_range')
    is_gsc=operation=='seo.search_console';prop=payload['property_id'];key='search_console_properties' if is_gsc else 'ga4_properties'
    if prop not in config.get(key,[]):return unavailable('google_property_not_authorized')
    if not is_gsc and not re.fullmatch('[0-9]{1,16}',prop):return unavailable('invalid_ga4_property')
    try:
        access=token(config,fetcher);headers={'Authorization':'Bearer '+access,'Content-Type':'application/json'}
        if is_gsc:
            endpoint='https://www.googleapis.com/webmasters/v3/sites/'+quote(prop,safe='')+'/searchAnalytics/query'
            body={'startDate':str(start),'endDate':str(end),'dimensions':['query','page'],'rowLimit':payload.get('limit',100)}
        else:
            endpoint='https://analyticsdata.googleapis.com/v1beta/properties/'+prop+':runReport'
            body={'dateRanges':[{'startDate':str(start),'endDate':str(end)}],'dimensions':[{'name':'sessionDefaultChannelGroup'}],'metrics':[{'name':'sessions'},{'name':'activeUsers'}],'limit':str(payload.get('limit',100))}
        raw=fetcher.fetch(endpoint,method='POST',headers=headers,body=json.dumps(body).encode())
        if raw.get('status')!=200:return response('degraded',reason={401:'google_authorization_failed',403:'google_access_denied',429:'google_api_rate_limited'}.get(raw.get('status'),'google_provider_http_error'),http_status=raw.get('status'),connection_verified=False)
        data=checked_json(raw)
        # Only data fields; never reflect auth/provider error payloads or credentials.
        allowed=('rows','responseAggregationType') if is_gsc else ('dimensionHeaders','metricHeaders','rows','rowCount','metadata','totals')
        selected={k:data[k] for k in allowed if k in data}
        selected['rows']=selected.get('rows',[])[:payload.get('limit',100)]
        return response(method='google_search_console' if is_gsc else 'google_analytics_data_api',property_id=prop,data=selected,
                        connection_verified=True,limitations=['Rows may be sampled, omitted, anonymized or limited; do not sum query rows as complete site totals'])
    except Exception as error:return response('degraded',reason=getattr(error,'code','google_connection_failed'),connection_verified=False)
