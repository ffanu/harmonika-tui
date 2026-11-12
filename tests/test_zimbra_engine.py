import copy
import unittest
from backend.zimbra.engine import parse_status,assess,controller_command,controller_state


def evidence():
    return {'status_exit':0,'services':[{'service':'ldap','state':'running'},{'service':'mailbox','state':'running'},{'service':'proxy','state':'running'},{'service':'mta','state':'running'}],
            'log_metadata':{'mode':'0755'},'log_access':{'read':True,'write':True,'traverse':True},
            'checks':{'web_https':{'ok':True,'status':200,'tls_verified':True,'login_page_verified':True},'smtp_ehlo':{'ok':True,'reply_code':250},'imap_tls':{'ok':True,'tls_verified':True,'greeting_ok':True}}}


class ZimbraEngineTests(unittest.TestCase):
    def test_parse_service_states_not_subordinate_error_lines(self):
        rows=parse_status('Host example.test\n\tldap                    Running\n\tmailbox                 Stopped\n\t\tzmmailboxdctl is not running.\n\tservice webapp          Stopped\n')
        self.assertEqual(rows,[{'service':'ldap','state':'running'},{'service':'mailbox','state':'stopped'},{'service':'service webapp','state':'stopped'}])

    def test_running_processes_do_not_override_bad_http(self):
        data=evidence();data['checks']['web_https']={'ok':False,'status':502}
        r=assess(data);self.assertFalse(r['healthy']);self.assertIn('ZIMBRA_PROXY_BACKEND_FAILURE',[x['code']for x in r['findings']])

    def test_zero_log_permissions_explain_false_service_status_but_not_automatic_fix(self):
        data=evidence();data['log_access']={k:False for k in data['log_access']};data['log_metadata']['mode']='0000'
        r=assess(data);self.assertFalse(r['healthy']);self.assertEqual(r['findings'][0]['code'],'ZIMBRA_LOG_ACCESS_DENIED')
        self.assertIn('only the affected directory',r['findings'][0]['next_action'])

    def test_successful_health_is_bounded_not_a_delivery_claim(self):
        r=assess(evidence());self.assertTrue(r['healthy']);self.assertFalse(r['delivery_tested']);self.assertFalse(r['external_mail_flow_tested'])

    def test_missing_states_nonzero_status_and_failed_tls_cannot_pass(self):
        for change in ('empty','exit','tls','service'):
            data=evidence()
            if change=='empty':data['services']=[]
            if change=='exit':data['status_exit']=1
            if change=='tls':data['checks']['imap_tls']['ok']=False
            if change=='service':data['services'][1]['state']='stopped'
            self.assertFalse(assess(data)['healthy'])

    def test_ok_booleans_without_verified_tls_and_reply_codes_do_not_pass(self):
        data=evidence();data['checks']['imap_tls']={'ok':True}
        self.assertFalse(assess(data)['healthy'])
        data=evidence();data['checks']['smtp_ehlo']['reply_code']=500
        self.assertFalse(assess(data)['healthy'])

    def test_ldap_only_is_not_an_all_in_one_mail_server_success(self):
        data=evidence();data['services']=[{'service':'ldap','state':'running'}]
        self.assertFalse(assess(data)['healthy'])

    def test_controller_uses_login_context_and_forbids_arbitrary_service_or_stop(self):
        self.assertEqual(controller_command('amavis','start'),"su - zimbra -c '/opt/zimbra/bin/zmamavisdctl start'")
        for component,operation in [('mailbox','stop'),('mailbox;sudo','start')]:
            with self.assertRaises(ValueError):controller_command(component,operation)

    def test_exit_zero_with_start_failure_is_a_domain_failure(self):
        r={'exit_status':0,'execution_state':'completed','connection_status':'connected',
           'output':"Starting amavisd...Can't locate Amavis/Boot.pm in @INC ... failed."}
        self.assertEqual(controller_state(r),'perl_context_or_module_failure')

    def test_generic_http_success_or_truncated_controller_is_incomplete(self):
        data=evidence();data['checks']['web_https'].pop('login_page_verified')
        self.assertFalse(assess(data)['healthy'])
        self.assertEqual(controller_state({'execution_state':'completed','connection_status':'connected',
            'exit_status':0,'output':'is running','output_truncated':True}),'unknown')

    def test_stats_status_on_stderr_and_mixed_collectors(self):
        result={'execution_state':'completed','connection_status':'connected','exit_status':0,
                'output':'','stderr':'Running: zmstat-cpu\nRunning: zmstat-fd\n'}
        self.assertEqual(controller_state(result),'running')
        result.update(exit_status=1,stderr=result['stderr']+'process 123 in /opt/zimbra/zmstat/pid/zmstat-fd-real.pid not running\n')
        self.assertEqual(controller_state(result),'stopped')

    def test_missing_or_orphaned_stats_receipt_cannot_be_hidden_by_running_status(self):
        data=evidence();data['services'].append({'service':'stats','state':'running'})
        data['stats_receipts']={'fd_wrapper_consistent':False,'orphaned_fd_collector':True}
        self.assertFalse(assess(data)['healthy'])
        self.assertIn('ZIMBRA_STATS_RECEIPT_INCONSISTENT',[r['code']for r in assess(data)['findings']])
        data['stats_receipts']['fd_wrapper_consistent']=True
        self.assertTrue(assess(data)['healthy'])


if __name__=='__main__':unittest.main()
