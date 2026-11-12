import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch,Mock
from backend.zimbra import engine
from backend.zimbra import knowledge
from backend.zimbra import tls
from backend.zimbra import renewal
from backend.zimbra import relay

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/tools'))
with patch.dict(sys.modules,{'zimbra_engine':engine,'zimbra_knowledge':knowledge,'zimbra_tls':tls,'zimbra_renewal':renewal,'zimbra_relay':relay}):
    spec=importlib.util.spec_from_file_location('zimbra_ops_test',ROOT/'runtime/tools/zimbra_ops.py')
    ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)


class AdapterTests(unittest.TestCase):
    def test_ssl_plan_rejects_private_or_unknown_remote_fields(self):
        run=Mock(return_value={'ok':True,'output':json.dumps({'ok':True,'cert_name':'mail.example.test','inventory':{'chat_history':'PRIVATE_SENTINEL'}})})
        ops.configure(lambda:{'mail':{'host':'192.0.2.1','vendor':'linux'}},run)
        with patch.object(ops.Path,'read_text',return_value='fixed_probe_source'),patch.object(ops,'build_fixed_probe',return_value='fixed_probe'):
            result=ops.ssl_plan({'target':'mail','cert_name':'mail.example.test','hostname':'mail.example.test'})
        self.assertFalse(result['ok']);self.assertNotIn('PRIVATE',json.dumps(result))

    def test_release_helper_imports_in_isolation_with_canonical_engine(self):
        package_spec=importlib.util.spec_from_file_location('zimbra_release',ROOT/'runtime/package-release.py')
        package=importlib.util.module_from_spec(package_spec);package_spec.loader.exec_module(package)
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory);package.copy_zimbra_tools(destination)
            self.assertEqual((destination/'zimbra_engine.py').read_bytes(),(ROOT/'backend/zimbra/engine.py').read_bytes())
            script="import sys;sys.path.insert(0,sys.argv[1]);import zimbra_ops;print(','.join(sorted(zimbra_ops.HANDLERS)))"
            result=subprocess.run([sys.executable,'-I','-c',script,directory],capture_output=True,text=True,check=True)
            self.assertEqual(result.stdout.strip(),'zimbra_component,zimbra_health,zimbra_mailjet_plan,zimbra_qa,zimbra_ssl_plan,zimbra_tls_audit')

    def test_unknown_disabled_and_non_linux_targets_never_execute(self):
        run=Mock();ops.configure(lambda:{'disabled':{'enabled':False},'win':{'vendor':'windows'}},run)
        for target in ('missing','disabled','win'):self.assertFalse(ops.health({'target':target})['ok'])
        run.assert_not_called()

    def test_credentials_stay_in_connector_and_bad_payload_is_not_health(self):
        run=Mock(return_value={'ok':True,'output':json.dumps({'ok':True,'services':'not-a-list'})})
        ops.configure(lambda:{'mail':{'host':'127.0.0.1','vendor':'linux','password':'PRIVATE_SENTINEL'}},run)
        result=ops.health({'target':'mail'})
        self.assertEqual(result['error'],'zimbra_probe_invalid_response')
        args=run.call_args.args;self.assertEqual(args[3],'PRIVATE_SENTINEL');self.assertLessEqual(len(args[4]),8192)
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(result));self.assertNotIn('PRIVATE_SENTINEL',args[4])

    def test_truncated_collection_cannot_return_health(self):
        run=Mock(return_value={'ok':True,'output_truncated':True,'execution_state':'completed'})
        ops.configure(lambda:{'mail':{'host':'127.0.0.1','vendor':'linux'}},run)
        self.assertFalse(ops.health({'target':'mail'})['ok'])

    def test_tls_adapter_rejects_unregistered_or_bad_domain_before_connection(self):
        run=Mock();ops.configure(lambda:{'mail':{'host':'192.0.2.1','vendor':'linux'}},run)
        for args in ({'target':'unknown','mail_domain':'example.test'},{'target':'mail','mail_domain':'-flag.test'},{'target':'mail','mail_domain':'bad\nexample.test'}):
            self.assertFalse(ops.tls_audit(args)['ok'])
        run.assert_not_called()

    def test_tls_bad_payload_never_echoes_private_fields(self):
        run=Mock(return_value={'ok':True,'output':json.dumps({'ok':True,'chat_history':'PRIVATE_SENTINEL'})})
        ops.configure(lambda:{'mail':{'host':'192.0.2.1','vendor':'linux','password':'PRIVATE_PASSWORD'}},run)
        result=ops.tls_audit({'target':'mail','mail_domain':'example.test'})
        self.assertEqual(result['error'],'tls_probe_invalid_response')
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('PRIVATE_PASSWORD',run.call_args.args[4])

    def test_start_requires_authorized_task_and_skips_running_component(self):
        run=Mock(return_value={'ok':True,'exit_status':0,'execution_state':'completed','connection_status':'connected','output':'amavisd is running.'})
        ops.configure(lambda:{'mail':{'host':'127.0.0.1','vendor':'linux'}},run)
        r=ops.component({'target':'mail','component':'amavis','operation':'start'})
        self.assertFalse(r['ok']);run.assert_not_called()
        r=ops.component({'target':'mail','component':'amavis','operation':'start','authorized_repair':True})
        self.assertEqual(r['state'],'already_running');self.assertFalse(r['executed']);self.assertEqual(run.call_count,1)

    def test_failed_start_never_becomes_success_from_exit_zero(self):
        run=Mock(side_effect=[{'exit_status':1,'execution_state':'completed','connection_status':'connected','output':'amavisd is not running.'},
                             {'exit_status':0,'execution_state':'completed','connection_status':'connected','output':"Can't locate Amavis/Boot.pm in @INC"}])
        ops.configure(lambda:{'mail':{'host':'127.0.0.1','vendor':'linux'}},run)
        r=ops.component({'target':'mail','component':'amavis','operation':'start','authorized_repair':True})
        self.assertFalse(r['ok']);self.assertEqual(r['state'],'perl_context_or_module_failure');self.assertTrue(r['executed'])


if __name__=='__main__':unittest.main()
