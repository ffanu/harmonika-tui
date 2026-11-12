"""Bounded, unauthenticated mail TLS/DNS observations. Never reads private keys."""
import concurrent.futures
import datetime
import hashlib
import http.client
import ipaddress
import json
import re
import shlex
import shutil
import smtplib
import socket
import ssl
import subprocess
import sys


def domain(value):
    if not isinstance(value,str) or len(value)>253:raise ValueError('invalid_domain')
    value=value.rstrip('.').lower()
    if '.' not in value or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',s)for s in value.split('.')):
        raise ValueError('invalid_domain')
    return value


def failure(exc):
    result={'ok':False,'error_type':type(exc).__name__}
    if isinstance(exc,ssl.SSLCertVerificationError):
        code=getattr(exc,'verify_code',None)
        result.update(tls_verified=False,verify_code=code,
            error_signature={9:'CERT_NOT_YET_VALID',10:'CERT_EXPIRED',18:'SELF_SIGNED',19:'SELF_SIGNED_CHAIN',
                             20:'UNTRUSTED_CHAIN',21:'UNTRUSTED_CHAIN',62:'HOSTNAME_MISMATCH',64:'HOSTNAME_MISMATCH'}.get(code,'CERT_VERIFY_FAILED'))
    elif isinstance(exc,(TimeoutError,socket.timeout)):result['error_signature']='CONNECTION_TIMEOUT'
    elif isinstance(exc,ConnectionRefusedError):result['error_signature']='CONNECTION_REFUSED'
    elif isinstance(exc,smtplib.SMTPServerDisconnected):result['error_signature']='CONNECTION_TIMEOUT'if 'timed out'in str(exc).lower()else'SMTP_DISCONNECTED'
    elif isinstance(exc,ssl.SSLError):result['error_signature']='TLS_HANDSHAKE_FAILED'
    else:result['error_signature']='PROBE_INCOMPLETE'
    return result


def certificate(sock):
    cert=sock.getpeercert();now=datetime.datetime.now(datetime.timezone.utc).timestamp()
    expires=ssl.cert_time_to_seconds(cert['notAfter']);starts=ssl.cert_time_to_seconds(cert['notBefore'])
    cipher=sock.cipher()
    return {'ok':True,'tls_verified':True,'hostname_verified':True,'chain_verified':True,
            'not_before':cert['notBefore'],'not_after':cert['notAfter'],
            'days_remaining':int((expires-now)//86400),'currently_valid':starts<=now<expires,
            'sha256':hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest(),
            'tls_version':sock.version(),'cipher':cipher[0] if cipher else None,
            'cipher_bits':cipher[2] if cipher else None}


def implicit(host,address,port,version=None):
    connection=None
    try:
        context=ssl.create_default_context()
        if version:
            if version=='TLSv1_3'and not getattr(ssl,'HAS_TLSv1_3',False):
                return {'ok':False,'error_signature':'CLIENT_TLS_VERSION_UNAVAILABLE'}
            selected=getattr(ssl.TLSVersion,version,None)
            if selected is None:return {'ok':False,'error_signature':'CLIENT_TLS_VERSION_UNAVAILABLE'}
            context.minimum_version=selected;context.maximum_version=selected
        with socket.create_connection((address,port),4) as plain:
            with context.wrap_socket(plain,server_hostname=host) as sock:
                sock.settimeout(4);result=certificate(sock)
                if port in (443,7071) and not version:
                    connection=http.client.HTTPConnection(address,port,timeout=4);connection.sock=sock
                    connection.request('HEAD','/',headers={'Host':host,'Connection':'close'})
                    response=connection.getresponse();hsts=response.getheader('Strict-Transport-Security')
                    match=re.search(r'(?:^|;)\s*max-age\s*=\s*(\d+)',hsts or '',re.I)
                    result.update(http_status=response.status,hsts_present=bool(hsts),hsts_max_age=int(match[1])if match else None)
                elif port==993:
                    greeting=sock.recv(2048);result['greeting_ok']=greeting.upper().startswith(b'* OK')
                    sock.sendall(b'h1 LOGOUT\r\n')
                elif port==465:
                    # SMTP_SSL connects to the checked SNI hostname through a fixed address.
                    smtp=smtplib.SMTP(timeout=4,local_hostname='harmonika-audit.invalid')
                    smtp.sock=sock;smtp.file=None;smtp._host=host
                    code,_=smtp.getreply();ehlo,_=smtp.ehlo('harmonika-audit.invalid')
                    result.update(greeting_code=code,ehlo_code=ehlo);smtp.quit()
                return result
    except Exception as exc:return failure(exc)
    finally:
        if connection:connection.close()


def starttls(host,address,port):
    # Postscreen can intentionally delay an external port25 greeting by several
    # seconds. A four-second client budget produced a false failure in this audit.
    smtp=smtplib.SMTP(timeout=12 if port==25 else 8,local_hostname='harmonika-audit.invalid')
    phase='connect'
    try:
        smtp._host=host;smtp.connect(address,port)
        phase='ehlo_before'
        before,_=smtp.ehlo('harmonika-audit.invalid');advertised=smtp.has_extn('starttls')
        if before!=250 or not advertised:
            return {'ok':False,'error_signature':'STARTTLS_NOT_ADVERTISED','ehlo_before':before,'starttls_advertised':advertised}
        phase='starttls';smtp.starttls(context=ssl.create_default_context())
        result=certificate(smtp.sock);phase='ehlo_after';after,_=smtp.ehlo('harmonika-audit.invalid')
        result.update(ehlo_before=before,starttls_advertised=True,ehlo_after=after,post_tls_ehlo_verified=after==250)
        result['ok']=result['ok'] and after==250
        smtp.quit();return result
    except Exception as exc:return {**failure(exc),'phase':phase}
    finally:smtp.close()


def dns_query(name,kind):
    executable=shutil.which('dig')
    if not executable:return {'state':'unavailable','error_signature':'DNS_TOOL_UNAVAILABLE'}
    try:
        p=subprocess.run([executable,'+time=2','+tries=1','+noall','+comments','+answer',name,kind],
                         capture_output=True,text=True,timeout=4)
        if len(p.stdout)>16384:return {'state':'unknown','error_signature':'DNS_OUTPUT_LIMIT'}
        header=re.search(r'status:\s*([A-Z]+)',p.stdout)
        if p.returncode!=0 or not header:return {'state':'unknown','error_signature':'DNS_QUERY_FAILED'}
        status=header[1]
        if status not in ('NOERROR','NXDOMAIN'):return {'state':'unknown','dns_status':status}
        answers=[]
        for line in p.stdout.splitlines():
            parts=line.split(None,4)
            if len(parts)==5 and not line.startswith(';') and parts[3]==kind:
                value=parts[4]
                if kind=='TXT':value=''.join(shlex.split(value))
                answers.append(value)
        return {'state':'observed','dns_status':status,'answers':answers[:32],
                'extended_error_codes':[int(x)for x in re.findall(r';\s*EDE:\s*(\d+)',p.stdout)][:8]}
    except Exception as exc:return {'state':'unknown','error_signature':'DNS_QUERY_FAILED','error_type':type(exc).__name__}


def dns_summary(host,mail_domain,address):
    requests={'a':(host,'A'),'aaaa':(host,'AAAA'),'mx':(mail_domain,'MX'),
              'spf':(mail_domain,'TXT'),'dmarc':('_dmarc.'+mail_domain,'TXT')}
    if ipaddress.ip_address(address).is_global:requests['ptr']=(ipaddress.ip_address(address).reverse_pointer,'PTR')
    out={}
    for key,(name,kind) in requests.items():
        value=dns_query(name,kind);answers=value.pop('answers',[])
        if key=='spf':
            records=[s for s in answers if re.match(r'^v=spf1(?:\s|$)',s,re.I)]
            value['record_count']=len(records)
            # No raw TXT or DMARC reporting addresses leave the probe.
            value['terminal_all']=[x for x in records[0].split() if re.fullmatch(r'[+?~\-]?all',x)] if len(records)==1 else []
            if len(records)==1:
                tokens=records[0].split()[1:]
                types={re.split(r'[:=/]',x.lstrip('+?~-'),1)[0]for x in tokens}
                known={'a','mx','ip4','ip6','include','redirect','exists','ptr','all','exp'}
                value['mechanism_types']=sorted(types&known)
                value['unrecognized_mechanism_count']=len(types-known)
                value['has_indirect_authorization']=bool(set(value['mechanism_types'])&{'a','mx','include','redirect','exists','ptr'})
                networks=[x.lstrip('+?~-')[4:]for x in tokens if x.lstrip('+?~-').startswith('ip4:')]
                try:value['explicit_ip4_matches_target']=any(ipaddress.ip_address(address) in ipaddress.ip_network(x,strict=False)for x in networks)
                except ValueError:value['explicit_ip4_matches_target']=None
            value['evaluation']='not_performed_no_sender_identity'
        elif key=='dmarc':
            records=[s for s in answers if re.match(r'^v=DMARC1(?:\s*;|$)',s)]
            value['record_count']=len(records)
            if len(records)==1:
                pairs=[s.strip().split('=',1)for s in records[0].split(';') if '=' in s]
                tags={k.strip():v.strip()for k,v in pairs}
                value['duplicate_tag_count']=len(pairs)-len(tags)
                value['policy']=tags.get('p')if not value['duplicate_tag_count']and tags.get('p')in ('none','quarantine','reject')else None
            value['scope']='exact_domain_record_only_no_tree_walk_or_alignment_test'
        else:value['answers']=answers
        out[key]=value
    return out


def system_readiness():
    result={}
    try:
        p=subprocess.run(['timedatectl','show','--property=NTPSynchronized','--value'],capture_output=True,text=True,timeout=3)
        result['clock_synchronized']=p.stdout.strip()=='yes'if p.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):result['clock_synchronized']=None
    fields={'LoadState','ActiveState','SubState','NextElapseUSecRealtime','LastTriggerUSec','Result','TimersCalendar'}
    try:
        p=subprocess.run(['systemctl','show','certbot.timer','--property='+','.join(sorted(fields)),'--no-pager'],capture_output=True,text=True,timeout=3)
        result['certbot_timer']={k:v[:256]for line in p.stdout.splitlines()if '='in line for k,v in [line.split('=',1)]if k in fields}
        result['timer_query_ok']=p.returncode==0
    except (OSError,subprocess.TimeoutExpired):result['timer_query_ok']=False
    try:
        p=subprocess.run(['certbot','--version'],capture_output=True,text=True,timeout=3)
        version=re.fullmatch(r'certbot\s+(\d+\.\d+\.\d+)\s*',p.stdout)
        result['certbot_version']=version[1]if version else None
    except (OSError,subprocess.TimeoutExpired):result['certbot_version']=None
    result['renewal_or_deploy_executed']=False
    return result


def probe(host=None,mail_domain=None,address='127.0.0.1',perspective='server_loopback',dns_address=None):
    if host is None:
        p=subprocess.run(['/bin/hostname','-f'],capture_output=True,text=True,timeout=3);host=p.stdout.strip()
    host=domain(host);mail_domain=domain(mail_domain or host)
    tasks={'https_443':lambda:implicit(host,address,443),
           'smtp_starttls_25':lambda:starttls(host,address,25),'submission_starttls_587':lambda:starttls(host,address,587),
           'submissions_tls_465':lambda:implicit(host,address,465),'imap_tls_993':lambda:implicit(host,address,993),
           'admin_https_7071':lambda:implicit(host,address,7071),
           'https_tls12':lambda:implicit(host,address,443,'TLSv1_2'),'https_tls13':lambda:implicit(host,address,443,'TLSv1_3')}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5)as pool:
        futures={name:pool.submit(fn)for name,fn in tasks.items()}
        checks={name:f.result()for name,f in futures.items()}
    return {'ok':True,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'hostname':host,'mail_domain':mail_domain,'perspective':perspective,'checks':checks,
            'dns':dns_summary(host,mail_domain,dns_address or address),'system':system_readiness()if perspective=='server_loopback'else{'renewal_or_deploy_executed':False},
            'client':{'ssl_library':ssl.OPENSSL_VERSION,'has_tls13':bool(getattr(ssl,'HAS_TLSv1_3',False))},'legacy_tls_enforcement':'not_tested',
            'revocation':'not_checked','dkim':'not_tested_selector_not_supplied','renewal_execution':'not_tested',
            'mail_sent':False,'authenticated':False,'relay_tested':False,'private_keys_read':False}


if __name__=='__main__':
    try:
        # Only a mail domain is accepted by the remote MCP adapter; sockets stay loopback.
        print(json.dumps(probe(mail_domain=sys.argv[1]if len(sys.argv)>1 else None,dns_address=sys.argv[2]if len(sys.argv)>2 else None)))
    except Exception as exc:print(json.dumps({'ok':False,'error_type':type(exc).__name__,'error':'tls_probe_incomplete'}))
