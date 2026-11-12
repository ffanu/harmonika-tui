import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('service_probe',ROOT/'runtime/tools/service_probe.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


class ServiceProbePrivacyTests(unittest.TestCase):
    def test_permissions_and_logrotate_errors_leave_only_mechanics(self):
        now=1800000000
        messages=['Permission denied for private-user@example.test password=PRIVATE_SENTINEL',
                  'error: duplicate log entry for /private/customer-file',
                  'parent directory has insecure permissions']
        raw='\n'.join(json.dumps({'MESSAGE':m,'__REALTIME_TIMESTAMP':str(now*1000000)})for m in messages).encode()
        r=probe.parse_journal(raw,now)
        self.assertEqual({x['signature']for x in r['signatures']},{'PERMISSION_DENIED','LOGROTATE_DUPLICATE_ENTRY','LOGROTATE_INSECURE_PARENT'})
        for secret in ('PRIVATE_SENTINEL','private-user','customer-file','MESSAGE'):
            self.assertNotIn(secret,json.dumps(r))

    def test_failed_service_can_be_completely_collected_without_claiming_health(self):
        def run(argv,**kw):
            data=b'LoadState=loaded\nActiveState=failed\nSubState=failed\nResult=exit-code\nExecMainStatus=1\nNRestarts=0\n' if argv[0]=='systemctl' else b''
            return {'exit_status':0,'error':'','data':data}
        r=probe.probe('zimbra.service',runner=run)
        self.assertTrue(r['ok']);self.assertEqual(r['state']['values']['ActiveState'],'failed');self.assertEqual(r['health_status'],'not_assessed')


if __name__=='__main__':unittest.main()
