import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

spec = importlib.util.spec_from_file_location('tui_control', Path(__file__).resolve().parents[1]/'runtime/tui-control/helper/tui_control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
PID = 'tui-'+'a'*40


class TuiControl(unittest.TestCase):
    def test_allowlist_refuses_arbitrary_hosts_create_and_execution_routes(self):
        for method, route in [('POST','/v1/media/jobs'),('POST','/v1/coding/jobs'),('GET','https://evil.invalid/'),('GET','/v1/core/sessions'),('GET','/v1/coding/jobs/a?token=private'),('POST','/v1/media/jobs/job_a/recover')]:
            self.assertFalse(control.route_allowed(method,route))
        self.assertTrue(control.route_allowed('POST','/v1/media/jobs/job_a/cancel'))

    def test_status_only_whitelisted_mechanics_not_provider_payloads_or_error_text(self):
        def request(method,route,**kwargs):
            return {'status':'ready','registered':100,'PRIVATE_SENTINEL':'private','diagnostics':{'graphics':{'status':'busy','endpoint':'PRIVATE_SENTINEL'},'registry_errors':{'raw':'PRIVATE_SENTINEL'}}}
        with patch.object(control,'request',side_effect=request):out=control.status()
        self.assertEqual(out['graphics']['status'],'busy');self.assertEqual(out['registry']['registered'],100)
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(out))

    def test_jobs_metadata_no_results_prompts_logs_or_false_progress(self):
        out=control.job_metadata('media','job_a',{'job':{'id':'job_a','status':'running','progress':'99%','result':{'text':'PRIVATE_SENTINEL'},'prompt':'PRIVATE_SENTINEL'}})
        self.assertIsNone(out['progress']);self.assertNotIn('PRIVATE_SENTINEL',json.dumps(out))
        with self.assertRaisesRegex(control.ControlError,'job_identity_mismatch'):
            control.job_metadata('media','job_a',{'job':{'id':'other','status':'completed'}})

    def test_cache_isolated_by_actor_and_project_and_never_stores_full_tool_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(control,'private_key',return_value='hai_synthetic_one_1234567890'):
            root=Path(tmp)/'state';one=control.Jobs(root)
            value=control.job_metadata('coding','job_a',{'id':'job_a','status':'queued','result':'PRIVATE_SENTINEL'});value['unexpected']='PRIVATE_SENTINEL'
            one.save(PID,value);self.assertEqual(len(one.list(PID)),1);self.assertEqual(one.list('tui-'+'b'*40),[])
            self.assertNotIn('PRIVATE_SENTINEL',one.path.read_bytes().decode('latin1'))
            with patch.object(control,'private_key',return_value='hai_synthetic_two_1234567890'):
                other=control.Jobs(root);self.assertEqual(other.list(PID),[])
            self.assertEqual(one.path.stat().st_mode&0o777,0o600)

    def test_cache_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(control,'private_key',return_value='hai_synthetic_one_1234567890'):
            root=Path(tmp)/'state';root.symlink_to(Path(tmp),target_is_directory=True)
            with self.assertRaisesRegex(control.ControlError,'unsafe_state_path'):control.Jobs(root)

    def test_stop_requires_confirmation_and_current_remote_ownership_state(self):
        store=Mock();store.save=Mock()
        args={'source':'coding','job_id':'job_a','project_id':PID}
        with patch.object(control,'request',return_value={'id':'job_a','status':'running'}) as request:
            with self.assertRaisesRegex(control.ControlError,'confirmation_required'):control.job_action('stop_job',args,store)
            self.assertEqual(request.call_count,1)
        with patch.object(control,'request',side_effect=[{'id':'job_a','status':'running'},{'id':'job_a','status':'stopped'}]) as request:
            result=control.job_action('stop_job',{**args,'confirmed':True},store)
            self.assertEqual(result['job']['status'],'stopped');self.assertEqual(request.call_args.args[1],'/v1/coding/jobs/job_a/stop')
        with patch.object(control,'request',return_value={'id':'job_a','status':'completed'}) as request:
            with self.assertRaisesRegex(control.ControlError,'job_stop_unavailable'):control.job_action('stop_job',{**args,'confirmed':True},store)
            self.assertEqual(request.call_count,1)

    def test_rtx_stop_uses_cancel_and_never_resubmits_job(self):
        with patch.object(control,'request',side_effect=[{'job':{'id':'job_a','status':'queued'}},{'job':{'id':'job_a','status':'cancelled'}}]) as request:
            control.job_action('stop_job',{'source':'media','job_id':'job_a','project_id':PID,'confirmed':True},Mock())
        self.assertEqual(request.call_args.args[:2],('POST','/v1/media/jobs/job_a/cancel'))

    def test_memory_explicit_consent_exact_idempotency_and_no_implicit_save(self):
        base={'action':'memory','operation':'set','project_id':PID,'key':'build','value':{'kind':'verified_command','text':'npm test'},'idempotency_key':'fixed-request-001'}
        with patch.object(control,'request') as request:
            with self.assertRaisesRegex(control.ControlError,'confirmation_required'):control.memory(base)
            request.assert_not_called()
        with patch.object(control,'request',return_value={'skill_id':'memory.set','status':'completed','data':{'stored':True}}) as request:
            self.assertTrue(control.memory({**base,'consent':True})['ok'])
        self.assertEqual(request.call_args.args[3],'fixed-request-001');self.assertNotIn('actor',request.call_args.args[2]['input'])

    def test_transport_auth_redirect_bad_json_and_unknown_mutation_are_safe(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'not json'
        opener=Mock();opener.open.return_value=response
        with patch.object(control,'private_key',return_value='hai_synthetic_key_123456789'),patch.object(control.urllib.request,'build_opener',return_value=opener):
            with self.assertRaisesRegex(control.ControlError,'bad_payload'):control.request('GET','/v1/core/capabilities')
            with self.assertRaisesRegex(control.ControlError,'submission_unknown'):control.request('POST','/v1/core/invoke',{'skill_id':'memory.set'})
            opener.open.side_effect=urllib.error.HTTPError('https://chat.harmonika.id',503,'PRIVATE_SENTINEL',{},io.BytesIO(b'PRIVATE_SENTINEL'))
            with self.assertRaisesRegex(control.ControlError,'submission_unknown'):control.request('POST','/v1/core/invoke',{'skill_id':'memory.clear'})
            opener.open.side_effect=urllib.error.HTTPError('https://chat.harmonika.id',401,'PRIVATE_SENTINEL',{},io.BytesIO(b'PRIVATE_SENTINEL'))
            with self.assertRaisesRegex(control.ControlError,'auth_required'):control.request('GET','/v1/core/capabilities')
        with self.assertRaisesRegex(control.ControlError,'redirect_refused'):control.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_project_identity_deterministic_and_not_the_private_path(self):
        one=control.project_id('/tmp/synthetic-project');self.assertEqual(one,control.project_id('/tmp/synthetic-project'))
        self.assertNotIn('synthetic',one);self.assertNotEqual(one,control.project_id('/tmp/other-project'))

    def test_malformed_mutation_ack_keeps_same_request_available_for_reconciliation(self):
        args={'action':'memory','operation':'clear','project_id':PID,'all':True,'idempotency_key':'fixed-clear-001'}
        with patch.object(control,'request',return_value={'status':'completed','data':{}}):
            with self.assertRaisesRegex(control.ControlError,'submission_unknown'):control.memory(args)

    def test_exit_status_is_mechanics_only_and_not_inferred_from_file_results(self):
        result={'id':'job_a','status':'completed','skill_id':'test.run','result':{'exit_code':1,'stdout':'PRIVATE_SENTINEL'}}
        value=control.job_metadata('coding','job_a',result);self.assertEqual(value['exit_code'],1);self.assertNotIn('PRIVATE_SENTINEL',json.dumps(value))
        result['skill_id']='fs.read';self.assertIsNone(control.job_metadata('coding','job_a',result)['exit_code'])


if __name__=='__main__':unittest.main()
