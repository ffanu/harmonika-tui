"""Evidence-based TLS audit verdicts; policy observations are not mail delivery."""
VERSION='1.2.0'
REQUIRED=('https_443','smtp_starttls_25','submission_starttls_587','imap_tls_993')


def valid_payload(data):
    allowed={'ok','observed_at','hostname','mail_domain','perspective','checks','dns','legacy_tls_enforcement',
             'revocation','dkim','renewal_execution','mail_sent','authenticated','relay_tested','private_keys_read','system','client'}
    if not isinstance(data,dict)or set(data)-allowed or data.get('ok')is not True:return False
    if any(data.get(key)is not False for key in ('mail_sent','authenticated','relay_tested','private_keys_read')):return False
    checks=data.get('checks');dns=data.get('dns')
    if (not isinstance(checks,dict)or set(checks)-set((*REQUIRED,'submissions_tls_465','admin_https_7071','https_tls12','https_tls13'))
        or not isinstance(dns,dict)or set(dns)-{'a','aaaa','mx','spf','dmarc','ptr'}):return False
    fields={'ok','error_type','tls_verified','verify_code','error_signature','hostname_verified','chain_verified',
            'not_before','not_after','days_remaining','currently_valid','sha256','tls_version','cipher','cipher_bits',
            'http_status','hsts_present','hsts_max_age','greeting_ok','greeting_code','ehlo_code','ehlo_before',
            'starttls_advertised','ehlo_after','post_tls_ehlo_verified','phase'}
    dns_fields={'state','error_signature','dns_status','extended_error_codes','record_count','terminal_all','evaluation',
                'mechanism_types','has_indirect_authorization','explicit_ip4_matches_target','unrecognized_mechanism_count',
                'policy','scope','answers','error_type','duplicate_tag_count'}
    if any(not isinstance(v,dict)or set(v)-fields for v in checks.values()):return False
    if any(not isinstance(v,dict)or set(v)-dns_fields for v in dns.values()):return False
    if any('answers'in dns.get(k,{})for k in ('spf','dmarc')):return False
    system=data.get('system',{});client=data.get('client',{})
    if not isinstance(system,dict)or set(system)-{'clock_synchronized','certbot_timer','timer_query_ok','renewal_or_deploy_executed','certbot_version'}:return False
    if not isinstance(client,dict)or set(client)-{'ssl_library','has_tls13'}:return False
    if 'certbot_timer'in system and (not isinstance(system['certbot_timer'],dict)or set(system['certbot_timer'])-{'LoadState','ActiveState','SubState','NextElapseUSecRealtime','LastTriggerUSec','Result','TimersCalendar'}):return False
    return True


def assess_tls(data):
    checks=data.get('checks',{});findings=[];verified=[]
    for name in (*REQUIRED,'submissions_tls_465'):
        check=checks.get(name,{})
        optional=name=='submissions_tls_465'
        if optional and check.get('error_signature')=='CONNECTION_REFUSED':continue
        valid=(check.get('ok')is True and check.get('tls_verified')is True and check.get('chain_verified')is True
               and check.get('hostname_verified')is True and check.get('currently_valid')is True
               and check.get('tls_version')in ('TLSv1.2','TLSv1.3'))
        if 'starttls' in name:valid=valid and check.get('post_tls_ehlo_verified')is True and check.get('ehlo_after')==250
        if name=='imap_tls_993':valid=valid and check.get('greeting_ok')is True
        if valid:
            verified.append(name)
            days=check.get('days_remaining')
            if type(days)is int and days<=30:
                findings.append({'code':'CERT_EXPIRING_SOON','severity':'warning','endpoint':name,'days_remaining':days,'qa_topic':'tls_certificate'})
        else:
            findings.append({'code':check.get('error_signature','TLS_EVIDENCE_INCOMPLETE'),'severity':'warning'if optional else'error',
                             'endpoint':name,'qa_topic':'smtp_starttls'if 'starttls'in name else'tls_certificate'})
    web=checks.get('https_443',{})
    if web.get('tls_verified')is True and web.get('hsts_present')is False:
        findings.append({'code':'HSTS_NOT_OBSERVED','severity':'advisory','qa_topic':'tls_policy_evidence'})
    admin=checks.get('admin_https_7071',{})
    if data.get('perspective')=='external_mac'and admin.get('http_status')==200:
        findings.append({'code':'ADMIN_HTTPS_REACHABLE_EXTERNAL','severity':'advisory','qa_topic':'external_perspective',
                         'interpretation':'Management HTTPS is reachable from this client; review intended admin access scope, not an automatic firewall-change authorization.'})
    dns=data.get('dns',{})
    for key in ('a','mx','spf','dmarc','ptr'):
        item=dns.get(key,{})
        if item.get('state')!='observed':
            findings.append({'code':'DNS_CHECK_INCOMPLETE','severity':'warning','check':key,'qa_topic':'dns_mail_policy'});continue
        if item.get('extended_error_codes'):
            findings.append({'code':'DNS_RESOLVER_EXTENDED_ERROR','severity':'warning','check':key,'qa_topic':'dns_mail_policy'})
        if key in ('spf','dmarc') and item.get('record_count')!=1:
            findings.append({'code':key.upper()+'_EXACT_RECORD_COUNT','severity':'warning','count':item.get('record_count'),
                             'qa_topic':'dns_mail_policy','scope':'exact_domain_only'})
        elif key in ('a','mx','ptr') and not item.get('answers'):
            findings.append({'code':'DNS_RECORD_NOT_OBSERVED','severity':'warning','check':key,'qa_topic':'dns_mail_policy'})
        if key=='dmarc'and item.get('record_count')==1 and item.get('policy')not in ('none','quarantine','reject'):
            findings.append({'code':'DMARC_POLICY_NOT_PARSED','severity':'warning','qa_topic':'dns_mail_policy'})
        if key=='spf'and item.get('explicit_ip4_matches_target')is False and item.get('has_indirect_authorization')is True:
            findings.append({'code':'SPF_INDIRECT_EVALUATION_REQUIRED','severity':'advisory','qa_topic':'dns_mail_policy',
                             'interpretation':'A different explicit ip4 does not prove SPF failure: a/mx/include/redirect may authorize the sender; full identity-aware evaluation was not performed.'})
        if key=='spf'and set(item.get('terminal_all',[]))&{'all','+all'}:
            findings.append({'code':'SPF_PERMISSIVE_ALL','severity':'warning','qa_topic':'dns_mail_policy'})
    system=data.get('system',{})
    if system.get('clock_synchronized')is False:
        findings.append({'code':'CLOCK_SYNC_NOT_CONFIRMED','severity':'warning','qa_topic':'tls_certificate'})
    timer=system.get('certbot_timer',{})
    if 'OnCalendar=*-*-01 'in timer.get('TimersCalendar',''):
        findings.append({'code':'CERTBOT_MONTHLY_SCHEDULE_REVIEW','severity':'warning','qa_topic':'renewal_readiness',
                         'interpretation':'Observed first-of-month schedule can leave a narrow renewal/retry window. Check installed-version renewal threshold, overrides and other schedulers before changing it; active timer is not renewal assurance.'})
    schedule='observed_certbot_schedule'if timer.get('ActiveState')=='active'and timer.get('NextElapseUSecRealtime')else'not_established'
    required_verified=data.get('ok')is True and all(name in verified for name in REQUIRED)
    return {'engine':'harmonika-zimbra-tls','version':VERSION,'ok':True,'collection_succeeded':data.get('ok')is True,
            'required_tls_verified':required_verified,'verified_endpoints':verified,'findings':findings,
            'status':'review_needed'if findings else'verified_within_scope'if required_verified else'incomplete',
            'security_certified':False,'delivery_tested':False,'relay_tested':False,'legacy_tls_enforcement_verified':False,
            'revocation_checked':False,'dkim_signing_verified':False,'renewal_success_verified':False,'renewal_schedule':schedule,
            'interpretation':'Verified chain/hostname/validity and handshakes from one network perspective only; DNS records, renewal timer and advertised capabilities are not proof of message delivery or future renewal.'}
