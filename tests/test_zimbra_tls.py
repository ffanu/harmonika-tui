import copy
import importlib.util
from pathlib import Path
import ssl
import sys
import unittest
from unittest.mock import Mock,patch
from backend.zimbra.tls import assess_tls,REQUIRED

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('tls_probe_test',ROOT/'runtime/tools/zimbra_tls_probe.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


def evidence():
    certificate={'ok':True,'tls_verified':True,'chain_verified':True,'hostname_verified':True,
                 'currently_valid':True,'tls_version':'TLSv1.3','days_remaining':50}
    checks={name:{**certificate}for name in REQUIRED}
    for name in ('smtp_starttls_25','submission_starttls_587'):
        checks[name].update(post_tls_ehlo_verified=True,ehlo_after=250)
    checks['imap_tls_993']['greeting_ok']=True
    checks['https_443']['hsts_present']=True
    checks['submissions_tls_465']={'ok':False,'error_signature':'CONNECTION_REFUSED'}
    dns={name:{'state':'observed','answers':['technical-value']}for name in ('a','mx','ptr')}
    dns.update(spf={'state':'observed','record_count':1},dmarc={'state':'observed','record_count':1,'policy':'none'})
    return {'ok':True,'checks':checks,'dns':dns}


class TLSAssessmentTests(unittest.TestCase):
    def test_optional_465_refused_and_dmarc_none_do_not_break_verified_required_tls(self):
        result=assess_tls(evidence())
        self.assertTrue(result['required_tls_verified']);self.assertEqual(result['findings'],[])
        self.assertFalse(result['delivery_tested']);self.assertFalse(result['legacy_tls_enforcement_verified'])
        self.assertFalse(result['renewal_success_verified']);self.assertFalse(result['dkim_signing_verified'])

    def test_boolean_success_without_certificate_evidence_is_incomplete(self):
        data=evidence();data['checks']['https_443']={'ok':True}
        self.assertFalse(assess_tls(data)['required_tls_verified'])

    def test_advertised_starttls_without_post_tls_ehlo_is_not_verified(self):
        data=evidence();data['checks']['smtp_starttls_25']['post_tls_ehlo_verified']=False
        self.assertFalse(assess_tls(data)['required_tls_verified'])

    def test_invalid_certificate_and_failed_imap_greeting_are_failures(self):
        for name,key in [('https_443','currently_valid'),('imap_tls_993','greeting_ok')]:
            data=evidence();data['checks'][name][key]=False
            self.assertFalse(assess_tls(data)['required_tls_verified'])

    def test_expiry_warns_without_inventing_expired_or_broken_application(self):
        data=evidence();data['checks']['https_443']['days_remaining']=7
        result=assess_tls(data);self.assertTrue(result['required_tls_verified'])
        self.assertIn('CERT_EXPIRING_SOON',[x['code']for x in result['findings']])

    def test_dns_timeout_is_incomplete_not_missing_record(self):
        data=evidence();data['dns']['spf']={'state':'unknown','error_signature':'DNS_QUERY_FAILED'}
        findings=assess_tls(data)['findings']
        self.assertIn('DNS_CHECK_INCOMPLETE',[x['code']for x in findings])
        self.assertNotIn('SPF_EXACT_RECORD_COUNT',[x['code']for x in findings])

    def test_indirect_spf_is_not_declared_failed_from_literal_mismatch(self):
        data=evidence();data['dns']['spf'].update(explicit_ip4_matches_target=False,has_indirect_authorization=True)
        findings=assess_tls(data)['findings']
        self.assertEqual(findings[0]['code'],'SPF_INDIRECT_EVALUATION_REQUIRED')
        self.assertEqual(findings[0]['severity'],'advisory')

    def test_failed_collection_and_timer_presence_never_become_renewal_or_tls_success(self):
        data=evidence();data['ok']=False
        self.assertFalse(assess_tls(data)['required_tls_verified'])
        data['ok']=True;data['system']={'certbot_timer':{'ActiveState':'active','NextElapseUSecRealtime':'future'}}
        result=assess_tls(data)
        self.assertEqual(result['renewal_schedule'],'observed_certbot_schedule');self.assertFalse(result['renewal_success_verified'])

    def test_active_monthly_timer_remains_a_renewal_window_warning(self):
        data=evidence();data['system']={'certbot_version':'1.21.0','certbot_timer':{
            'ActiveState':'active','NextElapseUSecRealtime':'future','TimersCalendar':'{ OnCalendar=*-*-01 00:00:00 ; next_elapse=future }'}}
        result=assess_tls(data)
        self.assertTrue(result['required_tls_verified']);self.assertFalse(result['renewal_success_verified'])
        self.assertIn('CERTBOT_MONTHLY_SCHEDULE_REVIEW',[x['code']for x in result['findings']])


class TLSProbeTests(unittest.TestCase):
    def test_certificate_failure_exposes_only_signature_not_exception_private_text(self):
        exc=ssl.SSLCertVerificationError(1,'PRIVATE_SENTINEL')
        exc.verify_code=62
        result=probe.failure(exc)
        self.assertEqual(result['error_signature'],'HOSTNAME_MISMATCH')
        self.assertNotIn('PRIVATE_SENTINEL',str(result))

    def test_smtp_uses_sni_hostname_and_repeats_ehlo_only_after_verified_starttls(self):
        events=[];smtp=Mock();sock=Mock();smtp.sock=sock
        smtp.connect.side_effect=lambda address,port:events.append(('connect',address,port,smtp._host))
        smtp.ehlo.side_effect=lambda name:(events.append('ehlo')or (250,b'ignored'))
        smtp.has_extn.return_value=True
        def upgrade(*,context):
            self.assertTrue(context.check_hostname);self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
            events.append('verified_starttls')
        smtp.starttls.side_effect=upgrade
        with patch.object(probe.smtplib,'SMTP',return_value=smtp),patch.object(probe,'certificate',return_value={'ok':True}):
            result=probe.starttls('mail.example.test','127.0.0.1',587)
        self.assertEqual(events,[('connect','127.0.0.1',587,'mail.example.test'),'ehlo','verified_starttls','ehlo'])
        self.assertTrue(result['post_tls_ehlo_verified'])
        smtp.login.assert_not_called();smtp.sendmail.assert_not_called();smtp.mail.assert_not_called();smtp.rcpt.assert_not_called()

    def test_dns_reporting_email_and_raw_txt_never_leave_summary(self):
        def query(name,kind):
            if name.startswith('_dmarc.'):return {'state':'observed','answers':['v=DMARC1; p=none; rua=mailto:PRIVATE_SENTINEL@example.test']}
            if kind=='TXT':return {'state':'observed','answers':['v=spf1 a mx ip4:192.0.2.99 ~all','PRIVATE_SENTINEL']}
            return {'state':'observed','answers':[]}
        with patch.object(probe,'dns_query',side_effect=query):
            result=probe.dns_summary('mail.example.test','example.test','192.0.2.1')
        self.assertNotIn('PRIVATE_SENTINEL',str(result));self.assertNotIn('mailto:',str(result))
        self.assertTrue(result['spf']['has_indirect_authorization']);self.assertFalse(result['spf']['explicit_ip4_matches_target'])
        self.assertEqual(result['dmarc']['policy'],'none')

    def test_domain_input_rejects_shell_flags_and_line_breaks(self):
        for text in ('-x.example','example.test\nPRIVATE','example..test','bad name.test'):
            with self.assertRaises(ValueError):probe.domain(text)

    def test_client_without_tls13_never_proves_server_tls13_disabled(self):
        with patch.object(probe.ssl,'HAS_TLSv1_3',False),patch.object(probe.socket,'create_connection')as connect:
            result=probe.implicit('mail.example.test','127.0.0.1',443,'TLSv1_3')
        connect.assert_not_called();self.assertEqual(result['error_signature'],'CLIENT_TLS_VERSION_UNAVAILABLE')

    def test_smtp_wrapped_timeout_stays_network_timeout_with_stage(self):
        smtp=Mock();smtp.connect.side_effect=probe.smtplib.SMTPServerDisconnected('Connection unexpectedly closed: timed out PRIVATE_SENTINEL')
        with patch.object(probe.smtplib,'SMTP',return_value=smtp)as constructor:
            result=probe.starttls('mail.example.test','192.0.2.1',25)
        self.assertEqual(constructor.call_args.kwargs['timeout'],12)
        self.assertEqual(result['phase'],'connect');self.assertEqual(result['error_signature'],'CONNECTION_TIMEOUT')
        self.assertNotIn('PRIVATE_SENTINEL',str(result))

    def test_fixed_probe_fits_existing_transport_boundary(self):
        sys.path.insert(0,str(ROOT/'runtime/tools'))
        from service_probe_transport import build_fixed_probe
        command=build_fixed_probe((ROOT/'runtime/tools/zimbra_tls_probe.py').read_text(),['example.test','192.0.2.1'])
        self.assertLessEqual(len(command),8192)


if __name__=='__main__':unittest.main()
