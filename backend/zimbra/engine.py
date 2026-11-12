"""Pure Zimbra diagnosis. Never infer mail delivery from a running process."""
import re
try:
    from .knowledge import guidance
except ImportError:
    from zimbra_knowledge import guidance

VERSION='1.4.3'
CONTROLLERS={'mailbox':'zmmailboxdctl','amavis':'zmamavisdctl','logger':'zmloggerctl','stats':'zmstatctl'}


def controller_command(component,operation):
    if component not in CONTROLLERS or operation not in ('start','status'):
        raise ValueError('unsupported_zimbra_operation')
    # A single fixed command inside the account's login environment, without nested interpolation.
    return "su - zimbra -c '/opt/zimbra/bin/"+CONTROLLERS[component]+' '+operation+"'"


def controller_state(result):
    # zmstatctl writes its status lines to stderr, including successful Running:.
    output=str(result.get('output',''))+'\n'+str(result.get('stderr',''))
    if (result.get('execution_state')!='completed' or result.get('connection_status')!='connected'
        or result.get('output_truncated') or result.get('stderr_truncated')):return 'unknown'
    if re.search(r'can.t locate .* in @INC',output,re.I):return 'perl_context_or_module_failure'
    if re.search(r'permission denied|operation not permitted',output,re.I):return 'permission_failure'
    if re.search(r'not running|is stopped',output,re.I):return 'stopped'
    if re.search(r'\bfailed\b|\berror:',output,re.I):return 'reported_failure'
    if result.get('exit_status')==0 and re.search(r'\bis running\b|already running|running\.|^Running:',output,re.I|re.M):return 'running'
    if result.get('exit_status')==0:return 'completed_unverified'
    return 'command_failed'


def parse_status(text):
    rows=[]
    for line in text.splitlines():
        match=re.fullmatch(r'\s+([A-Za-z][A-Za-z0-9 ]{0,70}?)\s{2,}(Running|Stopped|Disabled)\s*',line)
        if match:rows.append({'service':match[1].strip(),'state':match[2].lower()})
    return rows


def assess(data):
    rows=data.get('services',[])
    stopped=[r['service'] for r in rows if r['state']=='stopped']
    access=data.get('log_access',{})
    permissions=all(access.get(k) is True for k in ('read','write','traverse'))
    checks=data.get('checks',{})
    web,smtp,imap=(checks.get(k,{})for k in ('web_https','smtp_ehlo','imap_tls'))
    protocols=(web.get('ok') is True and web.get('tls_verified') is True
               and web.get('login_page_verified') is True and web.get('status')==200
               and smtp.get('ok') is True and smtp.get('reply_code')==250
               and imap.get('ok') is True and imap.get('tls_verified') is True and imap.get('greeting_ok') is True)
    core={'ldap','mailbox','mta','proxy'}<={r['service'] for r in rows if r['state']=='running'}
    receipts=data.get('stats_receipts',{})
    stats_enabled=any(r['service']=='stats' and r['state']!='disabled' for r in rows)
    receipts_ok=not stats_enabled or receipts.get('fd_wrapper_consistent') is True
    healthy=data.get('status_exit')==0 and core and not stopped and permissions and protocols and receipts_ok
    findings=[]
    if not permissions:
        findings.append({'code':'ZIMBRA_LOG_ACCESS_DENIED','severity':'critical',
            'evidence':{'mode':data.get('log_metadata',{}).get('mode'),'effective_access':access},
            'next_action':'Verify the installed vendor permission rule, back up metadata/ACL, then correct only the affected directory. Never apply recursive chmod as a shortcut.'})
    if stopped:findings.append({'code':'ZIMBRA_COMPONENTS_STOPPED','services':stopped,'severity':'critical',
        'next_action':('Reconcile the orphan fd collector before starting stats again; a generic duplicate start cannot restore its wrapper. Follow scoped process-control authorization and preserve healthy services.'
            if 'stats' in stopped and receipts.get('orphaned_fd_collector') is True else
            'Recheck status after fixing prerequisites; start only missing components through official Zimbra controls. Do not kill unrelated PIDs or restart healthy services.')})
    if checks.get('web_https',{}).get('status') in (502,503,504):
        findings.append({'code':'ZIMBRA_PROXY_BACKEND_FAILURE','severity':'critical','next_action':'A listening proxy is not a healthy mailbox backend. Verify mailboxd, backend listeners and effective runtime-directory access.'})
    if not rows:findings.append({'code':'ZIMBRA_STATUS_INCOMPLETE','severity':'unknown'})
    if stats_enabled and not receipts_ok:
        findings.append({'code':'ZIMBRA_STATS_RECEIPT_INCONSISTENT','severity':'warning',
            'orphaned_fd_collector':receipts.get('orphaned_fd_collector') is True,
            'next_action':'Verify wrapper and root collector identities and sample progress. Never delete a receipt or invent a replacement PID to hide the mismatch. Reconcile before retry; respect process-control authorization.'})
    return {'engine':'harmonika-zimbra','version':VERSION,'application_health':'healthy' if healthy else 'unhealthy_or_incomplete',
            'healthy':healthy,'findings':findings,'recovery_guidance':guidance(findings),'checks_complete':all(k in checks for k in ('web_https','smtp_ehlo','imap_tls')),
            'delivery_tested':False,'external_mail_flow_tested':False,'content_is_untrusted':True,
            'interpretation':'Health covers reported components, effective log access, web TLS/HTTP and SMTP/IMAP handshakes; it is not proof of message delivery, mailbox contents, backups or external DNS/reputation.'}


if __name__=='__main__':
    import json,sys
    if sys.argv[1:]==['--capabilities']:
        print(json.dumps({'engine':'harmonika-zimbra','version':VERSION,'operations':['parse_status','health_assessment','controller_command','controller_state'],
            'live_adapters':['zimbra_health','zimbra_component','zimbra_qa','zimbra_tls_audit'],'repair_execution':'fixed authorized component start or existing plan-bound network_config',
            'reads_mailbox':False,'logs_private_content':False}))
