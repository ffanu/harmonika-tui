import unittest
from backend.zimbra import relay as r


class RelayTests(unittest.TestCase):
    def test_account_verification_checked_before_generating_secret(self):
        flags=dict(requiresEmailVerification=True,requiresBusinessVerification=False,isMaster=True,canCreateApiKey=True,isBasicOrFreePlan=True,apikeysLimit=2)
        result=r.assess_account(flags)
        self.assertEqual(result['blockers'],['MAILJET_ACCOUNT_EMAIL_VERIFICATION_REQUIRED'])
        self.assertFalse(result['key_generation_ready'])
        flags['requiresEmailVerification']=False
        self.assertTrue(r.assess_account(flags)['key_generation_ready'])

    def test_missing_account_evidence_or_api_permission_never_means_ready(self):
        self.assertFalse(r.assess_account({})['key_generation_ready'])
        self.assertFalse(r.assess_account({'requiresEmailVerification':False,'canCreateApiKey':False})['key_generation_ready'])
        self.assertNotIn('PRIVATE_SENTINEL',str(r.assess_account({'email':'PRIVATE_SENTINEL','chat_history':'PRIVATE_SENTINEL'})))

    def facts(self):return dict(tls_verified=True,credentials_ready=True,smtp_authenticated=True,sender_domains_verified=True,map_types=['lmdb'])

    def test_api_key_only_cannot_activate_relay(self):
        facts=self.facts();facts.update(credentials_ready=False,smtp_authenticated=False)
        result=r.plan(facts)
        self.assertFalse(result['ready_to_configure']);self.assertFalse(result['active'])
        self.assertIn('MAILJET_SECRET_REFERENCE_REQUIRED',result['blockers'])

    def test_exact_bracket_port_lmdb_and_tls_verify_policy(self):
        result=r.plan(self.facts());self.assertTrue(result['ready_to_configure'])
        self.assertEqual(result['map_contract']['lookup_key'],result['smtp']['relayhost'])
        self.assertEqual(result['smtp']['relayhost'],'[in-v3.mailjet.com]:587')
        self.assertIn('secure match=in-v3.mailjet.com',result['map_contract']['tls_policy'])

    def test_conflicting_routing_cannot_be_overwritten_silently(self):
        for field in ('existing_relay_conflict','existing_sasl_maps_conflict','existing_tls_maps_conflict','sender_dependent_routing'):
            self.assertFalse(r.plan({**self.facts(),field:True})['ready_to_configure'])

    def test_authenticated_does_not_mean_delivered_or_ready_without_senders(self):
        facts=self.facts();facts['sender_domains_verified']=False
        self.assertFalse(r.plan(facts)['ready_to_configure'])
        self.assertEqual(r.classify_smtp(235,'auth')['state'],'authenticated')
        self.assertFalse(r.classify_smtp(235,'auth')['message_sent'])
        self.assertEqual(r.classify_smtp(250,'data')['state'],'accepted_not_delivered')

    def test_smtp_errors_do_not_trigger_duplicate_messages_or_auth_retries(self):
        for code in (421,450,535,550,554):self.assertFalse(r.classify_smtp(code,'auth')['retry_safe'])

    def test_private_input_never_enters_plan_or_error(self):
        result=r.plan({**self.facts(),'api_key':'PRIVATE_SENTINEL','chat_history':'PRIVATE_SENTINEL'})
        self.assertNotIn('PRIVATE_SENTINEL',str(result))
        with self.assertRaisesRegex(ValueError,'invalid_mailjet_credentials'):
            r.validate_credentials({'api_key':'bad\n','secret_key':'PRIVATE_SENTINEL'})

    def test_installed_map_type_checked_no_generic_hash_assumption(self):
        facts=self.facts();facts['map_types']=['cidr']
        self.assertIn('INSTALLED_LMDB_SUPPORT_REQUIRED',r.plan(facts)['blockers'])


if __name__=='__main__':unittest.main()
