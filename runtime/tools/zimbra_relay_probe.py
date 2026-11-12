"""Fixed non-secret Zimbra/Mailjet probe. No AUTH, mail, map lookup or key read."""
import hashlib
import json
import shlex
import smtplib
import ssl
import subprocess


def postconf(key):
    result=subprocess.run(['/usr/bin/su','-','zimbra','-c',shlex.join(['/opt/zimbra/common/sbin/postconf','-h',key])],capture_output=True,text=True,timeout=12)
    if result.returncode:raise ValueError('postfix_probe_failed')
    return result.stdout.strip()


def probe():
    relay=postconf('relayhost');sasl=postconf('smtp_sasl_password_maps');tls=postconf('smtp_tls_policy_maps')
    p=subprocess.run(['/usr/bin/su','-','zimbra','-c','/opt/zimbra/common/sbin/postconf -m'],capture_output=True,text=True,timeout=12)
    maps=[v for v in p.stdout.split()if v in ('lmdb','hash','btree','proxy','texthash')]
    verified=False
    try:
        with smtplib.SMTP('in-v3.mailjet.com',587,timeout=12)as c:
            before=c.ehlo()[0];c.starttls(context=ssl.create_default_context());after=c.ehlo()[0]
            verified=before==250 and after==250
    except Exception:pass
    return {'tls_verified':verified,'credentials_ready':False,'smtp_authenticated':False,
            'sender_domains_verified':False,'map_types':maps,
            'existing_relay_conflict':bool(relay)and relay!='[in-v3.mailjet.com]:587',
            'existing_sasl_maps_conflict':bool(sasl)and sasl!='lmdb:/etc/harmonika-mailjet/sasl_passwd',
            'existing_tls_maps_conflict':bool(tls)and tls!='lmdb:/etc/harmonika-mailjet/tls_policy',
            'sender_dependent_routing':bool(postconf('sender_dependent_relayhost_maps')),
            'active':relay=='[in-v3.mailjet.com]:587'and postconf('smtp_sasl_auth_enable')=='yes'}


if __name__=='__main__':
    try:print(json.dumps({'ok':True,'inventory':probe()}))
    except Exception:print(json.dumps({'ok':False,'error':'mailjet_preflight_failed'}))
