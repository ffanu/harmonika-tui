import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.quality_audit import activate_gateway as activation


class ActivationGuard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.stage=self.root/'candidate';self.stage.mkdir()
        self.live=self.root/'original-gateway';self.live.write_bytes(b'original fixture')
        (self.root/'gateway-candidate').write_bytes(b'candidate fixture')
        (self.root/'quality-build-proof.json').write_text(json.dumps({'instrumentation_link_verified':True,'binary_sha256':activation.sha(self.root/'gateway-candidate')}))
        (self.stage/'manifest.json').write_text(json.dumps({'baseline':{},'candidate':{}}))
        (self.stage/'overlay.json').write_text('{"Replace":{}}')
        self.argv=['activate_gateway.py','--candidate-dir',str(self.stage)]

    def tearDown(self):self.tmp.cleanup()

    def command(self,*args):
        if 'ExecStart' in args:return '{ path='+str(self.live)+' ; argv[]=not-displayed ; }'
        if args[0]=='ss':return 'synthetic established connection'
        raise AssertionError('unexpected operation')

    def prepare(self):
        with patch.object(sys,'argv',self.argv),patch.object(activation,'DROPIN_PATH',self.root/'quality-audit.conf'),patch.object(activation,'command',side_effect=self.command),patch.object(activation,'pid',return_value='777'),patch('builtins.print'):
            activation.main()

    def test_default_only_prepares_reviewable_plan(self):
        self.prepare();plan=json.loads((self.stage/'activation-plan.json').read_text())
        self.assertEqual(plan['activation'],'pending_explicit_restart_approval')
        self.assertEqual(self.live.read_bytes(),b'original fixture')
        self.assertEqual(plan['services_to_restart'],['harmonika-web-chat-live.service'])

    def test_changed_process_refuses_activation(self):
        self.prepare()
        with patch.object(sys,'argv',self.argv+['--apply']),patch.object(activation.os,'geteuid',return_value=0),patch.object(activation,'LOCK_PATH',self.root/'lock'),patch.object(activation,'pid',return_value='888'):
            with self.assertRaisesRegex(ValueError,'gateway_baseline_changed'):activation.main()
        self.assertEqual(self.live.read_bytes(),b'original fixture')

    def test_unverified_or_different_build_never_prepared(self):
        (self.root/'quality-build-proof.json').write_text(json.dumps({'instrumentation_link_verified':False,'binary_sha256':activation.sha(self.root/'gateway-candidate')}))
        with self.assertRaisesRegex(ValueError,'verified_instrumented_build_required'):self.prepare()
        self.assertFalse((self.stage/'activation-plan.json').exists())

    def test_busy_gateway_never_forced_or_restarted(self):
        self.prepare()
        with patch.object(sys,'argv',self.argv+['--apply']),patch.object(activation.os,'geteuid',return_value=0),patch.object(activation,'LOCK_PATH',self.root/'lock'),patch.object(activation,'pid',return_value='777'),patch.object(activation,'command',side_effect=self.command) as command,patch.object(activation.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'gateway_busy'):activation.main()
            self.assertFalse(any('restart' in c.args for c in command.call_args_list))
        self.assertEqual(self.live.read_bytes(),b'original fixture')


if __name__=='__main__':unittest.main()
