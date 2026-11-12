"""Fixed root-side read-only health checks. No mailbox/log/config contents leave host."""
import datetime
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import smtplib
import socket
import ssl
import stat
import subprocess


def command(argv,timeout=35):
    try:
        p=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout)
        return p.returncode,p.stdout[:20000]
    except Exception:return None,''


def web_health(host):
    # Zimbra deployments can mount the login app at / instead of /zimbra/.
    # Inspect bounded unauthenticated content in memory; never return HTML/cookies.
    attempts=[]
    for path in ('/','/zimbra/'):
        conn=None
        try:
            sock=ssl.create_default_context().wrap_socket(socket.create_connection(('127.0.0.1',443),6),server_hostname=host)
            cert=sock.getpeercert();conn=http.client.HTTPConnection('127.0.0.1',443,timeout=8);conn.sock=sock
            conn.request('GET',path,headers={'Host':host,'Connection':'close','Accept':'text/html'})
            response=conn.getresponse();status=response.status;body=response.read(131072)
            marker=bool(re.search(rb'zLoginForm|Zimbra Web Client (?:Sign In|Log In)',body,re.I))
            result={'ok':status==200 and marker,'status':status,'path':path,'login_page_verified':marker,
                    'tls_verified':True,'certificate_not_after':cert.get('notAfter')}
            attempts.append({'path':path,'status':status,'login_page_verified':marker})
            if result['ok']:return {**result,'route_attempts':attempts}
        except Exception as exc:
            result={'ok':False,'path':path,'error_type':type(exc).__name__}
            attempts.append({'path':path,'error_type':type(exc).__name__})
        finally:
            if conn:conn.close()
    return {**result,'route_attempts':attempts}


def stats_receipts():
    out={}
    for name in ('zmstat-fd','zmstat-fd-real'):
        item={};p=Path('/opt/zimbra/zmstat/pid')/(name+'.pid')
        try:
            if p.is_symlink() or not p.is_file():raise ValueError('invalid_receipt')
            with p.open() as handle:value=handle.read(33)
            if len(value)>32:raise ValueError('invalid_receipt')
            value=value.strip()
            if not value.isdigit() or not 1<int(value)<2**31:raise ValueError('invalid_pid')
            item['pid']=int(value);proc=Path('/proc')/value;item['process_exists']=proc.exists()
            if item['process_exists']:
                item['comm']=(proc/'comm').read_text().strip()[:64]
                status=(proc/'status').read_text()
                uid=re.search(r'^Uid:\s+(\d+)',status,re.M);state=re.search(r'^State:\s+([A-Z])',status,re.M)
                item['uid']=int(uid[1]) if uid else None;item['state']=state[1] if state else None
        except (OSError,ValueError) as exc:item['error_type']=type(exc).__name__
        out[name]=item
    wrapper,collector=out['zmstat-fd'],out['zmstat-fd-real']
    try:zuid=pwd.getpwnam('zimbra').pw_uid
    except KeyError:zuid=None
    def live(item,uid):
        return (uid is not None and item.get('process_exists') is True and item.get('comm')=='zmstat-fd'
                and item.get('uid')==uid and item.get('state') not in (None,'Z','X'))
    out['fd_wrapper_consistent']=live(wrapper,zuid) and live(collector,0)
    out['orphaned_fd_collector']=live(collector,0) and not live(wrapper,zuid)
    try:
        sample=Path('/opt/zimbra/zmstat/fd.csv').stat()
        out['fd_sample']={'bytes':sample.st_size,'modified_at':datetime.datetime.fromtimestamp(sample.st_mtime,datetime.timezone.utc).isoformat()}
    except OSError:out['fd_sample']={'available':False}
    return out


def probe():
    code,host=command(['/bin/hostname','-f'],5);host=host.strip()
    if code!=0 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,250}',host):
        return {'ok':False,'error':'invalid_zimbra_hostname'}
    code,raw=command(['su','-','zimbra','-c','/opt/zimbra/bin/zmcontrol status'])
    services=[]
    for line in raw.splitlines():
        m=re.fullmatch(r'\s+([A-Za-z][A-Za-z0-9 ]{0,70}?)\s{2,}(Running|Stopped|Disabled)\s*',line)
        if m:services.append({'service':m[1].strip(),'state':m[2].lower()})
    metadata={};access={}
    try:
        s=os.lstat('/opt/zimbra/log');metadata={'mode':format(stat.S_IMODE(s.st_mode),'04o'),'uid':s.st_uid,'gid':s.st_gid,'is_directory':stat.S_ISDIR(s.st_mode),'is_symlink':stat.S_ISLNK(s.st_mode)}
        for name,flag in [('read','-r'),('write','-w'),('traverse','-x')]:
            rc,_=command(['/usr/sbin/runuser','-u','zimbra','--','/usr/bin/test',flag,'/opt/zimbra/log'],5);access[name]=rc==0
    except OSError:pass
    checks={}
    checks['web_https']=web_health(host)
    try:
        with smtplib.SMTP('127.0.0.1',25,timeout=6) as smtp:
            rc,_=smtp.ehlo('harmonika-audit.invalid');checks['smtp_ehlo']={'ok':rc==250,'reply_code':rc,'starttls_advertised':smtp.has_extn('starttls'),'mail_sent':False}
    except Exception as exc:checks['smtp_ehlo']={'ok':False,'error_type':type(exc).__name__,'mail_sent':False}
    try:
        with ssl.create_default_context().wrap_socket(socket.create_connection(('127.0.0.1',993),6),server_hostname=host) as sock:
            sock.settimeout(6);greeting=sock.recv(2048);good=greeting.upper().startswith(b'* OK');sock.sendall(b'h1 LOGOUT\r\n')
            checks['imap_tls']={'ok':good,'tls_verified':True,'greeting_ok':good,'authenticated':False}
    except Exception as exc:checks['imap_tls']={'ok':False,'error_type':type(exc).__name__,'authenticated':False}
    ports={}
    for port in (25,443,587,993,7071,8080,8443,7025):
        try:
            with socket.create_connection(('127.0.0.1',port),.8):ports[str(port)]=True
        except OSError:ports[str(port)]=False
    return {'ok':True,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hostname':host,
            'status_exit':code,'services':services,'log_metadata':metadata,'log_access':access,'checks':checks,'local_ports':ports,'stats_receipts':stats_receipts(),
            'mailbox_read':False,'credentials_read':False,'mail_sent':False}


if __name__=='__main__':
    try:print(json.dumps(probe()))
    except Exception:print(json.dumps({'ok':False,'error':'zimbra_probe_failed'}))
