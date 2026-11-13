import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1]/'backend/ocr_rtx/ollama_runtime.py'
spec = importlib.util.spec_from_file_location('ocr_runtime_tests', PATH)
runtime = importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def test_runtime_is_local_private_and_does_not_inherit_secrets_or_proxy(self):
        env = runtime.environment({'OPENAI_API_KEY': 'fixture', 'ACCESS_TOKEN': 'fixture',
                                   'CUSTOM_SECRET': 'fixture', 'PASSWORD': 'fixture',
                                   'PATH': 'fixture-path', 'HTTPS_PROXY': 'http://example.invalid'})
        for key in ('OPENAI_API_KEY', 'ACCESS_TOKEN', 'CUSTOM_SECRET', 'PASSWORD'):
            self.assertNotIn(key, env)
        self.assertEqual(env['OLLAMA_HOST'], '127.0.0.1:11439')
        self.assertEqual(env['OLLAMA_NO_CLOUD'], '1')
        self.assertEqual(env['OLLAMA_NOHISTORY'], '1')
        self.assertEqual(env['HTTPS_PROXY'], '')
        self.assertEqual(env['OLLAMA_NUM_PARALLEL'], '1')
        self.assertEqual(env['OLLAMA_MAX_LOADED_MODELS'], '1')
        self.assertTrue(env['OLLAMA_MODELS'].startswith('C:'))


if __name__ == '__main__':
    unittest.main()
