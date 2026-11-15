"""Examiner-owned local server: real concurrent HTTP and private SQLite checks."""
import concurrent.futures
import contextlib
import base64
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
import uuid


def main():
    project = Path.cwd()
    sys.path.insert(0, str(project))
    import app
    spec = importlib.util.spec_from_file_location('level5_acceptance', Path(__file__).with_name('acceptance.py'))
    os.environ['LEVEL5_URL'] = 'http://127.0.0.1:1'
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    acceptance.ORIGIN = None
    capture = io.StringIO()
    checks = {}
    with tempfile.TemporaryDirectory(prefix='level5-direct-') as temp, contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        database = Path(temp) / 'app.sqlite'
        server = app.make_server(str(database))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        acceptance.BASE = 'http://127.0.0.1:' + str(server.server_address[1])
        try:
            bad_password = 'PRIVATE-L5-WRONG-PASS'
            checks['invalid_login_rejected'] = acceptance.Client().request('/api/login', {'username':'alpha','password':bad_password})[0] == 401
            client = acceptance.Client()
            checks['login'] = client.login('alpha')[0] == 200
            values = [c.value for c in client.jar]
            cookie = '; '.join(c.name + '=' + c.value for c in client.jar)
            key = 'direct-' + uuid.uuid4().hex
            payload = {'title': 'PRIVATE-L5-LOG-SENTINEL', 'description': 'PRIVATE-L5-DESCRIPTION'}
            barrier = threading.Barrier(6)

            def create(_):
                barrier.wait(timeout=10)
                return acceptance.Client().request('/api/tickets', payload, headers={'Cookie': cookie, 'Idempotency-Key': key})

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(create, range(6)))
            successes = [r for r in responses if r[0] in [200, 201]]
            checks['parallel_one_creation'] = sum(r[0] == 201 for r in responses) == 1
            checks['parallel_one_id'] = bool(successes) and len({r[1]['id'] for r in successes}) == 1
            checks['parallel_bounded_conflicts'] = all(r[0] in [200, 201, 503] for r in responses)
            checks['tenant_header_without_cookie_rejected'] = acceptance.Client().request('/api/tickets', headers={'X-Tenant-ID':'alpha'})[0] == 401
            status, rows, _ = client.request('/api/tickets', headers={'X-Tenant-ID':'beta'})
            checks['tenant_header_cannot_override_session'] = status == 200 and len(rows['tickets']) == 1 and rows['tickets'][0]['id'] == successes[0][1]['id']
            _, _, cors_headers = client.request('/api/me', headers={'Origin':'https://untrusted.invalid'})
            checks['no_permissive_cors'] = cors_headers.get('Access-Control-Allow-Origin') not in ['*','https://untrusted.invalid']
            if successes and 'version' in successes[0][1]:
                ticket = successes[0][1]
                patch_barrier = threading.Barrier(6)

                def patch(_):
                    patch_barrier.wait(timeout=10)
                    return acceptance.Client().request('/api/tickets/' + str(ticket['id']), {'version': ticket['version'], 'priority':'high'}, method='PATCH', headers={'Cookie':cookie})

                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                    patched = list(pool.map(patch, range(6)))
                checks['parallel_patch_one_winner'] = sum(r[0] == 200 for r in patched) == 1
                checks['parallel_patch_bounded_conflicts'] = all(r[0] in [200,409,503] for r in patched)
                status, latest, _ = client.request('/api/tickets/' + str(ticket['id']))
                checks['parallel_patch_increments_once'] = status == 200 and latest['version'] == ticket['version'] + 1 and latest['priority'] == 'high'
            with sqlite3.connect(database, timeout=.25) as db:
                checks['one_durable_receipt'] = db.execute('SELECT count(*) FROM requests WHERE owner=? AND key=?', ('alpha', key)).fetchone()[0] == 1
                checks['one_durable_ticket'] = db.execute('SELECT count(*) FROM tickets').fetchone()[0] == 1
                checks['wal'] = db.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
                hashes = db.execute('SELECT session_hash FROM sessions').fetchall()
                checks['stored_session_hash_only'] = bool(values) and {row[0] for row in hashes} == {hashlib.sha256(value.encode()).hexdigest() for value in values}
                # Additional technical columns are allowed by the original contract.
                session_rows = list(db.execute('SELECT * FROM sessions'))
                audit_rows = list(db.execute('SELECT * FROM audit'))
                private = [*values, payload['title'], payload['description'], 'demo-alpha', bad_password]
                representations = private + [base64.b64encode(value.encode()).decode() for value in private] + [value.encode().hex() for value in private]
                checks['no_fixture_input_in_session_or_audit'] = all(value not in str(row) for row in session_rows + audit_rows for value in representations)
                checks['audit_mechanics_only'] = all(type(row[0]) is int and isinstance(row[1],str) and re.fullmatch('[A-Za-z0-9_.:-]{1,80}', row[1]) and (row[2] is None or type(row[2]) is int) and type(row[3]) is int for row in db.execute('SELECT id,action,ticket_id,ts FROM audit'))
                db.execute('BEGIN IMMEDIATE')
                started = time.monotonic()
                status, body, headers = client.request('/api/tickets', payload, headers={'Idempotency-Key': 'locked-' + key})
                elapsed = time.monotonic() - started
                checks['busy_503'] = status == 503 and body == {'error': 'database_busy'} and headers.get('Retry-After') == '1'
                checks['busy_bounded_latency'] = elapsed < 1.5
                db.rollback()
                checks['busy_did_not_write'] = db.execute('SELECT count(*) FROM requests WHERE key=?', ('locked-' + key,)).fetchone()[0] == 0 and db.execute('SELECT count(*) FROM tickets').fetchone()[0] == 1
                checks['integrity'] = db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            checks['recovered_after_lock'] = client.request('/api/requests/' + key)[0] == 200
            checks['health'] = client.request('/healthz')[0] == 200
            checks['ready'] = client.request('/readyz')[0] == 200
            log = capture.getvalue()
            checks['no_private_input_in_log'] = all(value not in log for value in [*values, payload['title'], payload['description'], 'demo-alpha', bad_password])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    result = {'passed': all(checks.values()), 'checks': checks, 'parallel_clients': 6, 'scope': 'own_local_server_synthetic_data'}
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'passed': False, 'failure_type': type(exc).__name__}))
        raise SystemExit(1)
