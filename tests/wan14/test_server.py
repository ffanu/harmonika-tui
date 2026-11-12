import http.client
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class ServiceBoundaryTests(unittest.TestCase):
    def test_import_has_no_existing_database_or_credential_side_effect(self):
        spec = importlib.util.spec_from_file_location('wan14_server_import', ROOT/'backend/high_video/server.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertIsNone(module.engine)
        self.assertIsNone(module.TOKEN)

    def test_http_auth_and_owner_checks_on_isolated_ephemeral_listener(self):
        spec = importlib.util.spec_from_file_location('wan14_server_boundary', ROOT/'backend/high_video/server.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        class Fake:
            def capabilities(self):
                return {'ok': True, 'fixture': True}
        module.TOKEN = 'synthetic-secret'; module.engine = Fake()
        server = module.Server(('127.0.0.1', 0), module.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for auth, expected in [('Bearer wrong', 401), ('Bearer \xff', 401), ('Bearer synthetic-secret', 200)]:
                c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                c.request('POST', '/rpc', json.dumps({'action': 'capabilities', 'owner': 'a'*64}),
                          {'Authorization': auth, 'Content-Type': 'application/json'})
                r = c.getresponse(); raw = r.read(); self.assertEqual(r.status, expected)
                if expected == 200:
                    self.assertTrue(json.loads(raw)['fixture'])
                c.close()
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            c.request('POST', '/rpc', json.dumps({'action': 'capabilities', 'owner': 'bad'}),
                      {'Authorization': 'Bearer synthetic-secret', 'Content-Type': 'application/json'})
            r = c.getresponse(); self.assertEqual(json.loads(r.read())['error']['code'], 'invalid_owner'); c.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(3)


if __name__ == '__main__':
    unittest.main()
