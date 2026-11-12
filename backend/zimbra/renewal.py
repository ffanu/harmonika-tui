"""Pure, scoped Certbot renewal planning. This module never runs service controls."""
import hashlib
import re

VERSION='1.3.0'
HOOK_PROOF="from certbot._internal import renewal,cli\nfrom certbot import configuration\nfrom certbot._internal.plugins import disco\nns=cli.prepare_and_parse_args(disco.PluginsRegistry.find_all(), ['renew','--cert-name','mail.example.test','--non-interactive','--no-directory-hooks','--pre-hook','/bin/true','--post-hook','/bin/true','--deploy-hook','/bin/true'])\nc=configuration.NamespaceConfig(ns)\nparams={k:'/bin/false' for k in ['pre_hook','post_hook','renew_hook']}\nrenewal.restore_required_config_elements(c,params)\nprint(int(all(getattr(c.namespace,k,None)=='/bin/true' and cli.set_by_cli(k) for k in params) and c.namespace.directory_hooks is False))\n"
HOOKS={'pre_hook','post_hook','deploy_hook','renew_hook'}
FIELDS={'authenticator','installer','renew_before_expiry','webroot_path','server','cert','chain','fullchain','privkey','archive_dir'}


def fqdn(value):
    if (not isinstance(value,str)or not 3<=len(value)<=128 or value!=value.lower()or '.'not in value
        or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',part)for part in value.split('.'))):
        raise ValueError('invalid_certificate_name')
    return value


def selected_config(text):
    """Certbot uses ConfigObj: top-level keys before [renewalparams] are valid.

    Return only mechanics. Never return account identifiers, emails, DNS provider
    credentials, arbitrary config text, or shell hook bodies.
    """
    if not isinstance(text,str)or len(text)>65536:raise ValueError('renewal_config_limit')
    section='root';result={'format':'certbot_configobj','fields':{},'hooks':{}};seen=set()
    for raw in text.splitlines():
        line=raw.strip()
        if not line or line.startswith(('#',';')):continue
        if line.startswith('['):section=line.strip('[] ').lower();continue
        if '='not in line:continue
        key,value=(part.strip()for part in line.split('=',1));key=key.lower().replace('-','_')
        if section not in ('root','renewalparams'):continue
        if key not in FIELDS|HOOKS:continue
        identity=(section,key)
        if identity in seen:raise ValueError('ambiguous_renewal_config')
        seen.add(identity)
        if key in HOOKS:
            result['hooks'][key]={'configured':bool(value),'sha256':hashlib.sha256(value.encode()).hexdigest()if value else None}
        elif key=='server':
            result['fields']['ca_environment']='staging'if 'staging'in value.lower()else'production'if value.startswith('https://acme-v02.api.letsencrypt.org/')else'other'
        elif key in ('authenticator','installer'):
            result['fields'][key]=value if re.fullmatch(r'[a-z0-9_:-]{1,64}',value)else'unknown'
        elif key=='renew_before_expiry':
            result['fields'][key]=value if re.fullmatch(r'\d+\s+(?:days?|hours?)',value)else'configured_unparsed'
        elif key=='webroot_path':result['fields']['webroot_configured']=bool(value)
        else:result['fields'][key+'_configured']=bool(value)
    return result


def validate_config(config):
    allowed={'cert_name','hostname','service_control_approved'}
    if not isinstance(config,dict)or set(config)-allowed:raise ValueError('invalid_renewal_config_fields')
    result={'cert_name':fqdn(config.get('cert_name')),'hostname':fqdn(config.get('hostname')),
            'service_control_approved':config.get('service_control_approved',False)}
    if type(result['service_control_approved'])is not bool:raise ValueError('invalid_service_approval')
    return result


def valid_inventory(data):
    """Fail closed before returning any remote field to an agent/public report."""
    keys={'authenticator','port80_listening','no_directory_hooks_supported','cli_hook_override_verified',
          'source_cert_valid','configured_command_hooks_present','directory_hooks','selected_config'}
    if not isinstance(data,dict)or set(data)!=keys:return False
    if data['authenticator']not in ('standalone','webroot','manual','unknown',None):return False
    if any(type(data[k])is not bool for k in keys-{'authenticator','directory_hooks','selected_config'}):return False
    hooks=data['directory_hooks'];cfg=data['selected_config']
    if not isinstance(hooks,list)or len(hooks)>96:return False
    for h in hooks:
        if not isinstance(h,dict)or set(h)!={'phase','name','sha256','safe_owner_mode','stops_proxy','restarts_zimbra','uses_lineage_env'}:return False
        if h['phase']not in ('pre','post','deploy')or not isinstance(h['name'],str)or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',h['name']):return False
        if not isinstance(h['sha256'],str)or not re.fullmatch('[0-9a-f]{64}',h['sha256']):return False
        if any(type(h[k])is not bool for k in ('safe_owner_mode','stops_proxy','restarts_zimbra','uses_lineage_env')):return False
    if not isinstance(cfg,dict)or set(cfg)!={'format','fields','hooks'}or cfg['format']!='certbot_configobj':return False
    if not isinstance(cfg['fields'],dict)or not isinstance(cfg['hooks'],dict):return False
    for key,val in cfg['fields'].items():
        if key in ('authenticator','installer'):
            if not isinstance(val,str)or not re.fullmatch(r'[a-z0-9_:-]{1,64}',val):return False
        elif key=='ca_environment':
            if val not in ('staging','production','other'):return False
        elif key=='renew_before_expiry':
            if not isinstance(val,str)or not re.fullmatch(r'\d+\s+(?:days?|hours?)|configured_unparsed',val):return False
        elif key in {'webroot_configured',*(k+'_configured'for k in FIELDS-{'authenticator','installer','server','webroot_path','renew_before_expiry'})}:
            if type(val)is not bool:return False
        else:return False
    for key,val in cfg['hooks'].items():
        if key not in HOOKS or not isinstance(val,dict)or set(val)!={'configured','sha256'}or type(val['configured'])is not bool:return False
        digest=val['sha256']
        if digest is not None and(not isinstance(digest,str)or not re.fullmatch('[0-9a-f]{64}',digest)):return False
    return True


def certbot_argv(config,dry_run=False):
    cfg=validate_config(config)
    if type(dry_run)is not bool:raise ValueError('invalid_dry_run')
    args=['/usr/bin/certbot','renew','--cert-name',cfg['cert_name'],'--non-interactive','--no-directory-hooks',
          '--pre-hook','/bin/true','--post-hook','/bin/true','--deploy-hook','/bin/true']
    if dry_run:args.append('--dry-run')
    return args


def plan(config,inventory):
    cfg=validate_config(config)
    if not isinstance(inventory,dict):raise ValueError('invalid_renewal_inventory')
    blockers=[];auth=inventory.get('authenticator')
    if auth not in ('standalone','webroot'):blockers.append('CHALLENGE_METHOD_REQUIRES_REVIEW')
    if inventory.get('cli_hook_override_verified')is not True or inventory.get('no_directory_hooks_supported')is not True:
        blockers.append('HOOK_ISOLATION_NOT_VERIFIED')
    needs_stop=auth=='standalone'and inventory.get('port80_listening')is True
    if needs_stop and not cfg['service_control_approved']:blockers.append('SCOPED_SSL_SERVICE_CONTROL_APPROVAL_REQUIRED')
    if auth=='webroot'and inventory.get('webroot_challenge_verified')is not True:blockers.append('WEBROOT_NOT_VERIFIED_FOR_ALL_EXISTING_SAN')
    if inventory.get('source_cert_valid')is not True:blockers.append('SOURCE_CERTIFICATE_REQUIRES_RECONCILIATION')
    return {'engine':'harmonika-zimbra-renewal','version':VERSION,'cert_name':cfg['cert_name'],
            'lineage':'/etc/letsencrypt/live/'+cfg['cert_name'],'schedule':'twice_daily_check_renew_only_when_due',
            'force_renewal':False,'requires_proxy_maintenance':needs_stop,'blockers':blockers,
            'ready_for_staging_test':not blockers,'production_activation_ready':False,
            'next_steps':['Private scoped backup and compare approved source hashes.',
                          'Staging ACME test with original hooks disabled; never deploy a staging certificate.',
                          'Verify production trust, hostname, SPKI key match and preserve every existing SAN.',
                          'Deploy only the intended lineage; skip unchanged deployed and served fingerprints.',
                          'Known failure uses verified official rollback; unknown execution requires reconciliation.',
                          'Activate scheduler only after staging/deploy checks and scoped service authorization.'],
            'interpretation':'An approved plan is not evidence of a successful renewal or deployment. This pure operation grants no permission and runs no commands.'}


def timer_units(config):
    cfg=validate_config(config);path='/etc/harmonika/zimbra-ssl/'+cfg['cert_name']+'.json'
    return {'service':('[Unit]\nDescription=Harmonika Zimbra SSL renewal\nAfter=network-online.target\nWants=network-online.target\n\n'
                       '[Service]\nType=oneshot\nUser=root\nGroup=root\nUMask=0077\nTimeoutStartSec=15min\n'
                       'ExecStart=/usr/bin/python3 -I /usr/local/lib/harmonika/zimbra_ssl_worker.py --config '+path+' --renew\n'
                       'StandardOutput=null\nStandardError=null\n'),
            'timer':('[Unit]\nDescription=Harmonika twice-daily SSL renewal checks\n\n[Timer]\n'
                     'OnCalendar=*-*-* 00,12:00:00\nRandomizedDelaySec=15min\nPersistent=true\n'
                     'Unit=harmonika-zimbra-ssl.service\n\n[Install]\nWantedBy=timers.target\n')}
