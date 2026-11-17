import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('delegate_activation', Path(__file__).resolve().parents[1]/'scripts/activate-agent-delegate.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

class GatewayActivationGuards(unittest.TestCase):
    def fixture(self, root):
        prod=root/'production';prod.mkdir();stage=root/'stage';app=stage/'app';app.mkdir(parents=True)
        for n,b in {'delegate_skill.go':b'old delegate','main.go':b'original gateway','harmonika-web':b'old binary'}.items(): (prod/n).write_bytes(b)
        for n,b in {'delegate_skill.go':b'new delegate','delegate_jobs.go':b'new jobs','main.go':b'original gateway'}.items(): (app/n).write_bytes(b)
        (stage/'harmonika-web-candidate').write_bytes(b'new binary')
        manifest={'stage':str(stage),'go_tests':{'passed':36,'failed':0,'skipped':0},'binary_sha256':M.digest(stage/'harmonika-web-candidate'),'source_sha256':{n:M.digest(app/n) for n in M.CHANGED}}
        (stage/'verified.json').write_text(json.dumps(manifest))
        baseline={'sources':{'delegate_skill.go':M.digest(prod/'delegate_skill.go')},'binary_sha256':M.digest(prod/'harmonika-web')}
        return prod,stage,baseline

    def test_active_work_refuses_restart_and_keeps_original_sources(self):
        with tempfile.TemporaryDirectory() as t:
            prod,stage,baseline=self.fixture(Path(t))
            with patch.object(M,'ROOT',prod),patch.object(M,'pid',return_value=123),patch.object(M.os,'readlink',return_value=str(prod)),patch.object(M,'idle',return_value=False),patch.object(M.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'gateway_busy_no_restart'):M.activate(stage,baseline)
                run.assert_not_called()
            self.assertEqual((prod/'harmonika-web').read_bytes(),b'old binary')
            self.assertFalse((prod/'delegate_jobs.go').exists())

    def test_concurrent_companion_change_refuses_candidate(self):
        with tempfile.TemporaryDirectory() as t:
            prod,stage,baseline=self.fixture(Path(t));(prod/'main.go').write_bytes(b'user changed gateway')
            with patch.object(M,'ROOT',prod),patch.object(M.subprocess,'run') as run:
                with self.assertRaisesRegex(AssertionError,'companion_source_changed'):M.activate(stage,baseline)
                run.assert_not_called()
            self.assertEqual((prod/'main.go').read_bytes(),b'user changed gateway')
            self.assertEqual((prod/'harmonika-web').read_bytes(),b'old binary')

if __name__=='__main__':unittest.main()
