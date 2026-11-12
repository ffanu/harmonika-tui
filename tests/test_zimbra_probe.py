import importlib.util
from pathlib import Path
import unittest
import tempfile
from unittest.mock import Mock,patch

spec=importlib.util.spec_from_file_location('zimbra_probe',Path(__file__).resolve().parents[1]/'runtime/tools/zimbra_probe.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


class WebRouteTests(unittest.TestCase):
    def check(self,responses):
        conns=[]
        for status,body in responses:
            response=Mock(status=status);response.read.return_value=body
            conn=Mock();conn.getresponse.return_value=response;conns.append(conn)
        with patch.object(probe.ssl,'create_default_context') as context,patch.object(probe.socket,'create_connection'),patch.object(probe.http.client,'HTTPConnection',side_effect=conns):
            context.return_value.wrap_socket.return_value.getpeercert.return_value={'notAfter':'future'}
            result=probe.web_health('mail.example.test')
        return result,conns

    def test_actual_root_login_is_healthy_without_wrong_context_request(self):
        result,conns=self.check([(200,b'<form id="zLoginForm">PRIVATE_NONCE</form>')])
        self.assertTrue(result['ok']);self.assertEqual(result['path'],'/')
        self.assertNotIn('PRIVATE_NONCE',str(result));conns[0].getresponse.return_value.read.assert_called_once_with(131072)

    def test_root_404_falls_back_to_known_zimbra_context(self):
        result,_=self.check([(404,b'missing'),(200,b'<form id="zLoginForm"></form>')])
        self.assertTrue(result['ok']);self.assertEqual(result['path'],'/zimbra/');self.assertEqual(len(result['route_attempts']),2)

    def test_generic_200_and_404_never_pass(self):
        result,_=self.check([(200,b'generic nginx'),(404,b'not found')])
        self.assertFalse(result['ok'])


class StatsReceiptTests(unittest.TestCase):
    def test_actual_identity_and_non_zombie_wrapper_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pids=root/'opt/zimbra/zmstat/pid';pids.mkdir(parents=True)
            for name,pid,uid in [('zmstat-fd',123,998),('zmstat-fd-real',124,0)]:
                (pids/(name+'.pid')).write_text(str(pid))
                proc=root/'proc'/str(pid);proc.mkdir(parents=True)
                (proc/'comm').write_text('zmstat-fd\n');(proc/'status').write_text(f'State:\tS (sleeping)\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
            def local_path(value):return root/str(value).lstrip('/')
            with patch.object(probe,'Path',side_effect=local_path),patch.object(probe.pwd,'getpwnam',return_value=Mock(pw_uid=998)):
                self.assertTrue(probe.stats_receipts()['fd_wrapper_consistent'])
                (root/'proc/123/comm').write_text('java\n')
                r=probe.stats_receipts();self.assertFalse(r['fd_wrapper_consistent']);self.assertTrue(r['orphaned_fd_collector'])
                (root/'proc/123/comm').write_text('zmstat-fd\n')
                (root/'proc/123/status').write_text('State:\tZ (zombie)\nUid:\t998\n')
                self.assertFalse(probe.stats_receipts()['fd_wrapper_consistent'])


if __name__=='__main__':unittest.main()
