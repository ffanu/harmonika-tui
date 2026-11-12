import copy
import unittest
from backend.zimbra import knowledge
from backend.zimbra.engine import assess


class RecoveryKnowledgeTests(unittest.TestCase):
    def test_index_and_lessons_are_callable_without_target_or_credentials(self):
        index=knowledge.lookup()
        self.assertEqual(len(index['topics']),24)
        for item in index['topics']:
            lesson=knowledge.lookup(item['id'])
            self.assertTrue(lesson['ok']);self.assertFalse(lesson['grants_authorization'])
            self.assertFalse(lesson['runs_commands']);self.assertFalse(lesson['trains_model'])

    def test_unrecognized_or_untrusted_topic_is_rejected_without_echo(self):
        for value in (None,[],{},'PRIVATE_SENTINEL;shutdown'):
            result=knowledge.lookup(value)
            self.assertFalse(result['ok']);self.assertNotIn('PRIVATE_SENTINEL',str(result))

    def test_provisioning_keeps_browser_account_and_relay_target_evidence_separate(self):
        lesson=knowledge.lookup('mailjet_provisioning')
        self.assertFalse(lesson['grants_authorization'])
        self.assertFalse(lesson['runs_commands'])
        self.assertIn('bukan bukti akun sudah aktif',lesson['answer'])
        steps=' '.join(lesson['steps'])
        for prerequisite in ('reference credential baru','chrome_apple_events_javascript_disabled',
                             'pemilik mengizinkan','alias SSH/DNS','SMTP AUTH melalui TLS'):
            self.assertIn(prerequisite,steps)
        self.assertIn('Account activation is required',steps)
        self.assertIn('hentikan loop Show/Generate',steps)

    def test_real_permission_failure_prioritizes_prerequisite_before_component_start(self):
        result=assess({'services':[{'service':'mailbox','state':'stopped'}],
                       'log_access':{'read':False,'write':False,'traverse':False},'checks':{}})
        self.assertEqual(result['recovery_guidance'][0]['topic'],'log_access')
        steps=result['recovery_guidance'][0]['steps']
        self.assertIn('Backup',steps[2]);self.assertIn('Uji akses',steps[3])

    def test_orphan_collector_requires_narrow_authorization_not_general_restart(self):
        result=assess({'services':[{'service':'stats','state':'running'}],
                       'stats_receipts':{'fd_wrapper_consistent':False,'orphaned_fd_collector':True},
                       'log_access':{'read':True,'write':True,'traverse':True},'checks':{}})
        lesson=next(x for x in result['recovery_guidance']if x['topic']=='stats_orphan')
        self.assertEqual(lesson['authorization'],'specific_process_control')
        self.assertFalse(lesson['grants_authorization'])
        self.assertIn('collector fd saja',lesson['steps'][2])
        self.assertIn('semua statistik',lesson['avoid'][1])

    def test_guidance_contains_only_reviewed_mechanics_not_supplied_customer_fields(self):
        data={'services':[],'checks':{},'customer':'PRIVATE_SENTINEL',
              'chat_history':['PRIVATE_SENTINEL'],'credentials':'PRIVATE_SENTINEL'}
        result=assess(data)
        self.assertNotIn('PRIVATE_SENTINEL',str(result['recovery_guidance']))
        self.assertNotIn('PRIVATE_SENTINEL',str(knowledge.lookup()))

    def test_duplicate_findings_do_not_duplicate_recovery_steps(self):
        findings=[{'code':'ZIMBRA_PROXY_BACKEND_FAILURE'}]*3
        self.assertEqual(len(knowledge.guidance(findings)),1)
        self.assertEqual(knowledge.guidance([{'code':'UNTRUSTED_UNKNOWN'}]),[])

    def test_stopped_stats_with_live_orphan_is_reconciled_before_generic_start_advice(self):
        result=assess({'services':[{'service':'stats','state':'stopped'}],
                       'stats_receipts':{'fd_wrapper_consistent':False,'orphaned_fd_collector':True},
                       'log_access':{'read':True,'write':True,'traverse':True},'checks':{}})
        self.assertEqual(result['recovery_guidance'][0]['topic'],'stats_orphan')
        self.assertIn('before starting stats again',result['findings'][0]['next_action'])


if __name__=='__main__':unittest.main()
