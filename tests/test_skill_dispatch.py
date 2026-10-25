import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('skill_launcher',Path(__file__).resolve().parents[1]/'runtime/harmonika.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Dispatch(unittest.TestCase):
    def test_fixed_helpers_forward_arguments_after_verification(self):
        for name,script in [('documents','documents.py'),('browser','smoke.cjs'),('terminal-qa','terminal_qa.py'),('async-concurrency','async_runtime.py'),('ai-integration','stream_protocol.py')]:
            with self.subTest(name=name),patch.object(m.sys,'argv',['harmonika','skill',name,'self-test']),patch.object(m,'active',return_value=(Path('/fixture'),{})),patch.object(m,'verify_files') as verify,patch.object(m.os,'execv') as execute:
                m.main();verify.assert_called_once();command=execute.call_args.args[1]
                index=2 if name=='documents' else 1
                self.assertTrue(command[index].endswith('/'+script));self.assertEqual(command[index+1:],['self-test'])
                if name=='documents':self.assertEqual(command[1],'-I')
    def test_unknown_helper_never_launches_native_application(self):
        with patch.object(m.sys,'argv',['harmonika','skill','../../private']),patch.object(m.os,'execv') as execute:
            with self.assertRaisesRegex(ValueError,'usage:'):m.main()
            execute.assert_not_called()

    def test_state_command_verified_before_dispatch(self):
        with patch.object(m.sys,'argv',['harmonika','state','status']),patch.object(m,'active',return_value=(Path('/fixture'),{})),patch.object(m,'verify_files') as verify,patch.object(m.os,'execv') as execute:
            m.main();verify.assert_called_once();self.assertEqual(execute.call_args.args[1],['/usr/bin/python3','/fixture/tools/state_store.py','status'])
    def test_network_and_clipboard_fixed_verified_dispatch(self):
        for name,script in [('network','tui_gateway.py'),('clipboard','clipboard.py')]:
            with self.subTest(name=name),patch.object(m.sys,'argv',['harmonika',name,'status']),patch.object(m,'active',return_value=(Path('/fixture'),{})),patch.object(m,'verify_files') as verify,patch.object(m.os,'execv') as execute:
                m.main();verify.assert_called_once();self.assertEqual(execute.call_args.args[1],['/usr/bin/python3','/fixture/tools/'+script,'status'])

    def test_skill_helpers_fail_closed_before_exec_on_verification_failure(self):
        for name in ('documents', 'browser', 'terminal-qa', 'async-concurrency', 'ai-integration'):
            with self.subTest(name=name),patch.object(m.sys,'argv',['harmonika','skill',name,'capabilities']),patch.object(m,'active',return_value=(Path('/fixture'),{})),patch.object(m,'verify_files',side_effect=ValueError('modified_runtime')),patch.object(m.os,'execv') as execute:
                with self.assertRaisesRegex(ValueError,'modified_runtime'):m.main()
                execute.assert_not_called()

    def test_shared_skills_verified_before_isolated_cli_dispatch(self):
        with patch.object(m.sys,'argv',['harmonika','skills','list']),patch.object(m,'active',return_value=(Path('/fixture'),{})),patch.object(m,'verify_files') as verify,patch.object(m.os,'execv') as execute:
            m.main();verify.assert_called_once()
            self.assertEqual(execute.call_args.args[1],['/usr/bin/python3','-I','/fixture/tools/skill_cli.py','list'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
