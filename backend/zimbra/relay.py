"""Pure Mailjet/Postfix rollout contract. Credentials never enter this planner."""
import re

VERSION='1.4.0'
HOST='in-v3.mailjet.com'
PORT=587
RELAY='['+HOST+']:'+str(PORT)
MAP='/etc/harmonika-mailjet/sasl_passwd'
TLS_MAP='/etc/harmonika-mailjet/tls_policy'
ATTRIBUTES={
    'zimbraMtaRelayHost':RELAY,
    'zimbraMtaSmtpSaslAuthEnable':'yes',
    'zimbraMtaSmtpSaslPasswordMaps':'lmdb:'+MAP,
    'zimbraMtaSmtpCnameOverridesServername':'no',
    'zimbraMtaSmtpSaslSecurityOptions':'noplaintext,noanonymous',
    'zimbraMtaSmtpSaslTlsSecurityOptions':'noanonymous',
    'zimbraMtaSmtpTlsPolicyMaps':'lmdb:'+TLS_MAP,
    'zimbraMtaSmtpTlsCAfile':'/etc/ssl/certs/ca-certificates.crt',
}


def assess_account(flags):
    """Account prerequisites come before clicking key-generation controls."""
    if not isinstance(flags,dict):flags={}
    observed={k:v for k,v in flags.items()if k in {'requiresEmailVerification','requiresBusinessVerification','isMaster','isBasicOrFreePlan','canCreateApiKey','apikeysLimit'}and type(v)in (bool,int)}
    blockers=[]
    if observed.get('requiresEmailVerification')is True:blockers.append('MAILJET_ACCOUNT_EMAIL_VERIFICATION_REQUIRED')
    if observed.get('requiresBusinessVerification')is True:blockers.append('MAILJET_ACCOUNT_BUSINESS_VERIFICATION_REQUIRED')
    if observed.get('canCreateApiKey')is False:blockers.append('MAILJET_ACCOUNT_API_PERMISSION_REQUIRED')
    if 'requiresEmailVerification'not in observed or 'canCreateApiKey'not in observed:blockers.append('ACCOUNT_PREREQUISITES_NOT_OBSERVED')
    return {'observed':observed,'blockers':blockers,'key_generation_ready':not blockers,
            'secret_generation_attempted':False,'email_address_returned':False,
            'interpretation':'Account/UI flags indicate prerequisites, not a captured rejected backend request. A free plan or a configured key limit alone does not prove API denial or exhausted quota.',
            'next_step':'Resolve the observed verification/access prerequisite, refresh account evidence, then generate once and capture directly into a private credential reference.'if blockers else'With explicit generation permission, generate once and verify the result; never replay an unknown credential mutation.'}


def plan(inventory):
    if not isinstance(inventory,dict):raise ValueError('invalid_relay_inventory')
    blockers=[]
    for key,code in [('tls_verified','MAILJET_TLS_NOT_VERIFIED'),('credentials_ready','MAILJET_SECRET_REFERENCE_REQUIRED'),
                     ('smtp_authenticated','MAILJET_SMTP_AUTH_REQUIRED'),('sender_domains_verified','MAILJET_SENDER_DOMAIN_VERIFICATION_REQUIRED')]:
        if inventory.get(key)is not True:blockers.append(code)
    if 'lmdb'not in inventory.get('map_types',[]):blockers.append('INSTALLED_LMDB_SUPPORT_REQUIRED')
    for field in ('existing_relay_conflict','existing_sasl_maps_conflict','existing_tls_maps_conflict','sender_dependent_routing'):
        if inventory.get(field)is True:blockers.append(field.upper()+'_REQUIRES_REVIEW')
    return {'ok':True,'engine':'harmonika-zimbra-mailjet','version':VERSION,
            'smtp':{'host':HOST,'port':PORT,'transport':'verified_STARTTLS_required','relayhost':RELAY},
            'credential_reference':'owner-local:mailjet','credentials_in_tool_arguments':False,
            'ready_to_configure':not blockers,'active':inventory.get('active')is True and not blockers,
            'blockers':blockers,'desired_server_attributes':dict(ATTRIBUTES),
            'map_contract':{'type':'lmdb','lookup_key':RELAY,'source':MAP,'compiled':MAP+'.lmdb',
                            'tls_source':TLS_MAP,'tls_policy':RELAY+' secure match='+HOST,
                            'source_and_db_permissions':'root:postfix 0640; parent root:postfix 0750',
                            'never_print_query_result':True},
            'rollout':['Authenticate over verified TLS before changing live routing. Never use HTTP Send API URL as relayhost.',
                       'Verify each envelope sender domain with the same Mailjet API key; preserve SPF/DKIM/DMARC policy.',
                       'Back up selected server/global attribute presence and private maps. Do not overwrite unrelated routing/maps.',
                       'Write private exact-key LMDB map using the installed Zimbra postmap; validate lookup internally without printing credentials.',
                       'Apply server-scoped zmprov attributes, not transient postconf -e or edits to generated main.cf.',
                       'Wait for zmconfigd regeneration, check effective settings and inbound restriction fingerprints; use official MTA reload only if needed.',
                       'Rollback exact prior attributes and maps if a confirmed failure occurs. Unknown execution requires reconciliation.',
                       'Only an authorized owned-recipient test can prove submission. SMTP235 or250 acceptance is not inbox delivery.'],
            'does_not_change':['inbound_MX','local_delivery','smtpd_relay_restrictions','mynetworks','mailboxes'],
            'logs':'timestamp,skill_id,error_type,latency only; no keys, contacts, messages or raw SMTP responses',
            'delivery_verified':False,'runs_commands':False}


def classify_smtp(code,phase):
    if type(code)is not int or phase not in ('connect','tls','auth','mail','rcpt','data'):
        return {'state':'unknown','retry_safe':False}
    if phase=='auth':
        return {'state':'authenticated'if code==235 else'auth_temporary_failure'if 400<=code<500 else'auth_rejected',
                'retry_safe':False,'message_sent':False}
    if phase=='data'and code==250:return {'state':'accepted_not_delivered','retry_safe':False}
    return {'state':'temporary_failure'if 400<=code<500 else'permanent_failure'if 500<=code<600 else'phase_complete',
            'retry_safe':False}


def validate_credentials(data):
    if not isinstance(data,dict)or set(data)!={'api_key','secret_key'}:raise ValueError('invalid_mailjet_credentials')
    if any(not isinstance(data[k],str)or not re.fullmatch(r'[A-Fa-f0-9]{32,128}',data[k])for k in data):
        raise ValueError('invalid_mailjet_credentials')
    if data['api_key']==data['secret_key']:raise ValueError('mailjet_keys_must_differ')
    return data
