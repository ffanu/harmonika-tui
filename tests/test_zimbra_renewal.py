import unittest
from backend.zimbra.renewal import selected_config,plan,certbot_argv,timer_units,validate_config


class RenewalPlanTests(unittest.TestCase):
    def facts(self):
        return {'authenticator':'standalone','port80_listening':True,'cli_hook_override_verified':True,
                'no_directory_hooks_supported':True,'source_cert_valid':True}

    def config(self):return {'cert_name':'mail.example.test','hostname':'mail.example.test'}

    def test_native_configobj_top_level_is_not_misclassified_as_corruption(self):
        result=selected_config('version = 1.21.0\ncert = /etc/letsencrypt/live/mail.example.test/cert.pem\n[renewalparams]\nauthenticator = standalone\naccount = PRIVATE_SENTINEL\npre_hook = sudo PRIVATE_SENTINEL\n')
        self.assertEqual(result['fields']['authenticator'],'standalone')
        self.assertTrue(result['hooks']['pre_hook']['configured'])
        self.assertNotIn('PRIVATE_SENTINEL',str(result));self.assertNotIn('sudo',str(result))

    def test_hook_isolation_and_occupied_http_challenge_require_reviewed_scope(self):
        result=plan(self.config(),self.facts())
        self.assertFalse(result['ready_for_staging_test']);self.assertIn('SCOPED_SSL_SERVICE_CONTROL_APPROVAL_REQUIRED',result['blockers'])
        cfg=self.config();cfg['service_control_approved']=True
        result=plan(cfg,self.facts());self.assertTrue(result['ready_for_staging_test']);self.assertFalse(result['production_activation_ready'])

    def test_old_fd_process_exception_does_not_authorize_ssl_service_actions(self):
        cfg=self.config();cfg['fd_recovery_approved']=True
        with self.assertRaises(ValueError):validate_config(cfg)

    def test_arbitrary_commands_paths_and_truthy_approval_are_rejected(self):
        for cfg in ({**self.config(),'cert_name':'../etc/passwd'},{**self.config(),'hostname':'mail.test; reboot'},
                    {**self.config(),'service_control_approved':'true'},{**self.config(),'shell':'unsafe'}):
            with self.assertRaises(ValueError):validate_config(cfg)

    def test_cli_targets_one_certificate_and_disables_both_hook_sources(self):
        args=certbot_argv(self.config(),True)
        self.assertIn('--cert-name',args);self.assertIn('--dry-run',args);self.assertIn('--no-directory-hooks',args)
        for flag in ('--pre-hook','--post-hook','--deploy-hook'):self.assertEqual(args[args.index(flag)+1],'/bin/true')
        self.assertNotIn('--force-renewal',args);self.assertNotIn('--run-deploy-hooks',args)

    def test_scheduler_checks_twice_daily_without_forcing_certificate_issuance(self):
        units=timer_units(self.config());self.assertIn('00,12:00:00',units['timer'])
        self.assertIn('Persistent=true',units['timer']);self.assertNotIn('--force-renewal',units['service'])

    def test_webroot_must_be_verified_for_every_existing_san(self):
        facts=self.facts();facts.update(authenticator='webroot',port80_listening=True)
        self.assertIn('WEBROOT_NOT_VERIFIED_FOR_ALL_EXISTING_SAN',plan(self.config(),facts)['blockers'])


if __name__=='__main__':unittest.main()
