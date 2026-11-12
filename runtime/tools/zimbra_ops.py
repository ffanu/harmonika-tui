"""Owner MCP adapter: fixed read-only Zimbra probe through registered SSH."""
import json
from pathlib import Path
from service_probe_transport import build_fixed_probe
from zimbra_engine import assess,controller_command,controller_state
from zimbra_knowledge import lookup,QA
from zimbra_tls import assess_tls,valid_payload
import ipaddress
import re
from zimbra_renewal import plan as renewal_plan,fqdn,valid_inventory
from zimbra_relay import plan as relay_plan

_load=None
_execute=None


def configure(load_targets,ssh_exec):
    global _load,_execute
    _load,_execute=load_targets,ssh_exec


def health(args):
    target=args.get('target')
    targets=_load() if _load else {}
    if target not in targets:return {'ok':False,'error':'target_not_found'}
    info=targets[target]
    if info.get('vendor')!='linux' or info.get('enabled') is False:return {'ok':False,'error':'zimbra_requires_enabled_linux_target'}
    cmd=build_fixed_probe(Path(__file__).with_name('zimbra_probe.py').read_text())
    r=_execute(info['host'],info.get('port',22),info.get('username','root'),info.get('password',''),cmd,timeout=95,
               hostkey_sha256=info.get('hostkey_sha256'),key_path=info.get('key_path'))
    if not r.get('ok') or r.get('output_truncated'):
        return {'ok':False,'error':'zimbra_probe_transport_failed','execution_state':r.get('execution_state'),'safe_to_retry':False}
    try:data=json.loads(r['output'])
    except (ValueError,KeyError):return {'ok':False,'error':'zimbra_probe_invalid_response'}
    if not isinstance(data,dict) or not data.get('ok'):return {'ok':False,'error':'zimbra_probe_incomplete'}
    rows=data.get('services')
    if (not isinstance(rows,list) or len(rows)>64 or any(not isinstance(row,dict) or
        not isinstance(row.get('service'),str) or row.get('state') not in ('running','stopped','disabled') for row in rows)
        or not isinstance(data.get('checks'),dict) or any(not isinstance(v,dict) for v in data['checks'].values())
        or not isinstance(data.get('log_access'),dict) or any(type(data['log_access'].get(k)) is not bool for k in ('read','write','traverse'))):
        return {'ok':False,'error':'zimbra_probe_invalid_response'}
    return {**assess(data),'ok':True,'target':target,'evidence':data,'hostkey_verification':r.get('hostkey_verification'),
            'interpretation_of_ok':'Probe collection succeeded; inspect healthy/application_health for the health verdict.'}


TOOLS=[{'name':'zimbra_health','description':'Read-only Zimbra health on a registered Linux target: component status, effective log-directory access, verified web HTTPS, SMTP EHLO and IMAP TLS greeting. No mailbox/config secrets/log content, authentication or message delivery. ok means probe collection; healthy is separate. Use before and after authorized repairs; never infer delivery from ports or systemd alone.',
        'inputSchema':{'type':'object','properties':{'target':{'type':'string'}},'required':['target'],'additionalProperties':False}}]
HANDLERS={'zimbra_health':health}


def component(args):
    target=args.get('target');targets=_load() if _load else {}
    if target not in targets:return {'ok':False,'error':'target_not_found'}
    info=targets[target];operation=args.get('operation','status');name=args.get('component')
    if info.get('vendor')!='linux' or info.get('enabled') is False:return {'ok':False,'error':'zimbra_requires_enabled_linux_target'}
    if operation=='start' and args.get('authorized_repair') is not True:
        return {'ok':False,'error':'explicit_existing_repair_authorization_required','executed':False}
    try:status_cmd=controller_command(name,'status');requested=controller_command(name,operation)
    except ValueError as exc:return {'ok':False,'error':str(exc),'executed':False}
    def execute(cmd,timeout):
        return _execute(info['host'],info.get('port',22),info.get('username','root'),info.get('password',''),cmd,timeout=timeout,
                        hostkey_sha256=info.get('hostkey_sha256'),key_path=info.get('key_path'))
    before=execute(status_cmd,35);state=controller_state(before)
    base={'target':target,'component':name,'state':state,'exit_status':before.get('exit_status'),
          'health_verified':False,'requires_health_check':True,'safe_to_retry':False}
    if operation=='status':return {**base,'ok':state in ('running','stopped','completed_unverified'),'executed':False}
    if state=='running':return {**base,'ok':True,'state':'already_running','executed':False}
    if state!='stopped':return {**base,'ok':False,'error':'component_state_requires_reconciliation','executed':False}
    response=execute(requested,120);state=controller_state(response)
    passed=state in ('running','completed_unverified')
    return {**base,'ok':passed,'state':'start_reported_unverified' if passed else state,
            'executed':True,'exit_status':response.get('exit_status'),'execution_state':response.get('execution_state'),
            'connection_status':response.get('connection_status')}


TOOLS.append({'name':'zimbra_component','description':'Use an official Zimbra controller in the zimbra login environment. status is read-only. start requires existing explicit user repair authorization and authorized_repair=true; first checks current status and skips already-running components. Only mailbox/amavis/logger/stats, no stop/restart/kill. Exit zero plus a printed failure remains failure. Output is technical states only; follow with zimbra_health, not a delivery claim.',
    'inputSchema':{'type':'object','properties':{'target':{'type':'string'},'component':{'type':'string','enum':['mailbox','amavis','logger','stats']},
      'operation':{'type':'string','enum':['status','start']},'authorized_repair':{'type':'boolean','default':False}},'required':['target','component','operation'],'additionalProperties':False}})
HANDLERS['zimbra_component']=component


def qa(args):
    return lookup(args.get('topic','index'))


TOOLS.append({'name':'zimbra_qa','description':'Reviewed Zimbra troubleshooting and TLS/DNS audit Q&A. Use topic=index to discover 23 lessons, then select the matching topic. No network or private data, no authorization grant or automatic command execution. zimbra_health also attaches relevant guidance to known findings.',
    'inputSchema':{'type':'object','properties':{'topic':{'type':'string','enum':['index',*QA],'default':'index'}},'additionalProperties':False}})
HANDLERS['zimbra_qa']=qa


def tls_audit(args):
    target=args.get('target');targets=_load()if _load else{}
    if target not in targets:return {'ok':False,'error':'target_not_found'}
    info=targets[target];mail_domain=args.get('mail_domain')
    if info.get('vendor')!='linux'or info.get('enabled')is False:return {'ok':False,'error':'zimbra_requires_enabled_linux_target'}
    if (not isinstance(mail_domain,str)or len(mail_domain)>128 or '.'not in mail_domain or
        any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?',s)for s in mail_domain.rstrip('.').split('.'))):
        return {'ok':False,'error':'invalid_mail_domain'}
    try:dns_ip=str(ipaddress.ip_address(info['host']))
    except ValueError:return {'ok':False,'error':'tls_audit_requires_registered_ip_for_ptr'}
    cmd=build_fixed_probe(Path(__file__).with_name('zimbra_tls_probe.py').read_text(),[mail_domain,dns_ip])
    r=_execute(info['host'],info.get('port',22),info.get('username','root'),info.get('password',''),cmd,timeout=95,
               hostkey_sha256=info.get('hostkey_sha256'),key_path=info.get('key_path'))
    if not r.get('ok')or r.get('output_truncated'):return {'ok':False,'error':'tls_probe_transport_failed','safe_to_retry':False}
    try:data=json.loads(r['output'])
    except (ValueError,KeyError):return {'ok':False,'error':'tls_probe_invalid_response'}
    if not valid_payload(data):return {'ok':False,'error':'tls_probe_invalid_response'}
    return {**assess_tls(data),'target':target,'evidence':data,'hostkey_verification':r.get('hostkey_verification')}


TOOLS.append({'name':'zimbra_tls_audit','description':'Read-only fixed mail TLS/DNS audit on one registered Linux IP target. Verify trust+hostname+expiry for HTTPS443, actual SMTP STARTTLS25/587 with post-TLS EHLO, optional implicit465 and IMAP993. Bounded DNS A/AAAA/MX/SPF/DMARC/PTR metadata; no reporting-email addresses. Runs on server loopback, not proof of external reachability, legacy TLS rejection, revocation, DKIM signing, relay policy, renewal or delivery. No AUTH/MAIL/RCPT/DATA, key reads or changes.',
    'inputSchema':{'type':'object','properties':{'target':{'type':'string'},'mail_domain':{'type':'string','maxLength':128}},'required':['target','mail_domain'],'additionalProperties':False}})
HANDLERS['zimbra_tls_audit']=tls_audit


def ssl_plan(args):
    target=args.get('target');targets=_load()if _load else{}
    if target not in targets:return {'ok':False,'error':'target_not_found'}
    info=targets[target]
    if info.get('vendor')!='linux'or info.get('enabled')is False:return {'ok':False,'error':'zimbra_requires_enabled_linux_target'}
    try:cert_name=fqdn(args.get('cert_name'));hostname=fqdn(args.get('hostname'))
    except ValueError:return {'ok':False,'error':'invalid_certificate_name'}
    source=Path(__file__).with_name('zimbra_renewal.py').read_text()+'\n'+Path(__file__).with_name('zimbra_renewal_probe.py').read_text()
    cmd=build_fixed_probe(source,[cert_name])
    result=_execute(info['host'],info.get('port',22),info.get('username','root'),info.get('password',''),cmd,timeout=45,
                    hostkey_sha256=info.get('hostkey_sha256'),key_path=info.get('key_path'))
    if not result.get('ok')or result.get('output_truncated'):return {'ok':False,'error':'renewal_preflight_transport_failed','safe_to_retry':False}
    try:data=json.loads(result['output'])
    except (ValueError,KeyError):return {'ok':False,'error':'renewal_preflight_invalid_response'}
    if not isinstance(data,dict)or set(data)!={'ok','cert_name','inventory'}or data.get('ok')is not True or data.get('cert_name')!=cert_name or not valid_inventory(data.get('inventory')):
        return {'ok':False,'error':'renewal_preflight_incomplete'}
    # This read-only tool never grants process authorization from model arguments.
    report=renewal_plan({'cert_name':cert_name,'hostname':hostname},data['inventory'])
    return {**report,'ok':True,'target':target,'inventory':data['inventory'],'operator_approval_not_granted_by_tool':True}


TOOLS.append({'name':'zimbra_ssl_plan','description':'Read-only selected Certbot preflight and scoped SSL renewal plan for a registered Linux target. Understand ConfigObj top-level renewal keys, hook hashes/stop/restart signatures and occupied HTTP80. Returns twice-daily renewal-only-when-due plan, blockers and required service authorization. No private key, account email or raw hook/config body; never renews, deploys, changes timers, stops or restarts services and never grants process authorization.',
    'inputSchema':{'type':'object','properties':{'target':{'type':'string'},'cert_name':{'type':'string','maxLength':128},'hostname':{'type':'string','maxLength':128}},'required':['target','cert_name','hostname'],'additionalProperties':False}})
HANDLERS['zimbra_ssl_plan']=ssl_plan


def mailjet_plan(args):
    target=args.get('target');targets=_load()if _load else{}
    if target not in targets:return {'ok':False,'error':'target_not_found'}
    info=targets[target]
    if info.get('vendor')!='linux'or info.get('enabled')is False:return {'ok':False,'error':'zimbra_requires_enabled_linux_target'}
    cmd=build_fixed_probe(Path(__file__).with_name('zimbra_relay_probe.py').read_text())
    r=_execute(info['host'],info.get('port',22),info.get('username','root'),info.get('password',''),cmd,timeout=95,
               hostkey_sha256=info.get('hostkey_sha256'),key_path=info.get('key_path'))
    if not r.get('ok')or r.get('output_truncated'):return {'ok':False,'error':'mailjet_probe_transport_failed'}
    try:
        data=json.loads(r['output']);inventory=data['inventory']
        fields={'tls_verified','credentials_ready','smtp_authenticated','sender_domains_verified','map_types','existing_relay_conflict',
                'existing_sasl_maps_conflict','existing_tls_maps_conflict','sender_dependent_routing','active'}
        if set(data)!={'ok','inventory'}or data['ok']is not True or not isinstance(inventory,dict)or set(inventory)!=fields:raise ValueError()
        if any(type(inventory[k])is not bool for k in fields-{'map_types'}):raise ValueError()
        if not isinstance(inventory['map_types'],list)or len(inventory['map_types'])>5 or any(v not in ('lmdb','hash','btree','proxy','texthash')for v in inventory['map_types']):raise ValueError()
    except (ValueError,KeyError,TypeError):return {'ok':False,'error':'mailjet_probe_invalid_response'}
    return {**relay_plan(inventory),'target':target,'auth_tested':False,'message_sent':False}


TOOLS.append({'name':'zimbra_mailjet_plan','description':'Read-only Zimbra10/Mailjet preflight on a registered Linux target: installed Postfix map types, routing conflicts, verified STARTTLS587, and exact-key LMDB/persistent-attribute rollout plan. No key reads, AUTH, sending, map credential lookup or mutation. Credentials and sender validation remain explicit blockers until privately tested. Use mailjet_credentials audit_account before generation; verify account/email/access prerequisites rather than repeatedly clicking Generate.',
    'inputSchema':{'type':'object','properties':{'target':{'type':'string'}},'required':['target'],'additionalProperties':False}})
HANDLERS['zimbra_mailjet_plan']=mailjet_plan
