#!/usr/bin/env python3
"""Verify the retained v1 image on schema v2, then restore the good v2 image."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(root, passed_report):
    if not json.loads(passed_report.read_text()).get('passed'):
        raise RuntimeError('v2_independent_grade_must_pass_first')
    output = root / 'backward-image-result.json'
    if output.exists():
        raise RuntimeError('preserve_existing_attempt')
    grader = load('level5_grader', REPO / 'scripts/level5-grade.py')
    remote = lambda request: grader.remote(root, request)
    before = remote({'op': 'status'})
    checkpoint = json.loads((root / 'project/CHECKPOINT.json').read_text())
    sentinel = json.loads((root / 'sentinel.json').read_text())
    (root / 'agent-final-ledger.json').write_text(json.dumps(remote({'op': 'ledger'}), indent=2))
    checks = {}
    checks['v2_ready_before'] = before['active_release'].startswith('app-v2') and before['readiness']['http_status'] == 200
    if not checks['v2_ready_before']:
        raise RuntimeError('good_v2_required')
    proxy = None
    try:
        remote({'op': 'activate', 'release': checkpoint['release'], 'request_id': 'examiner-old-code'})
        for _ in range(20):
            status = remote({'op': 'status'})
            if status['readiness']['http_status'] == 200:
                break
            time.sleep(1)
        checks['actual_v1_image'] = status['image_id'] == checkpoint['image_id'] and status['active_release'] == checkpoint['release']
        checks['v1_ready_on_schema2'] = status['readiness']['http_status'] == 200 and status['readiness']['body'].get('schema_version') == 2
        db = remote({'op': 'db-audit'})
        row = next((x for x in db['rows'] if x['id'] == sentinel['ticket']['id']), None)
        checks['v1_preserves_v2_database'] = db['schema_version'] == 2 and db['integrity'] == 'ok' and row is not None and row.get('priority') == 'normal'
        proxy = subprocess.Popen([sys.executable, str(REPO / 'scripts/level5-lab/proxy.py'), str(root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.environ['LEVEL5_URL'] = json.loads(proxy.stdout.readline())['url']
        acceptance = load('level5_acceptance_backward', REPO / 'scripts/level5-lab/acceptance.py')
        acceptance.ORIGIN = 'examiner'
        client = acceptance.Client()
        checks['v1_login'] = client.login('alpha')[0] == 200
        code, item, _ = client.request('/api/tickets/' + str(sentinel['ticket']['id']))
        checks['v1_reads_old_record'] = code == 200 and all(item.get(k) == sentinel['ticket'][k] for k in ['id', 'title', 'description', 'status'])
        code, body, _ = client.request('/api/attachments/' + sentinel['attachment']['id'])
        checks['v1_download_exact_bytes'] = code == 200 and body == base64.b64decode(sentinel['content_b64'])
        code, item, _ = client.request('/api/tickets', sentinel['payload'], headers={'Idempotency-Key': 'migration-sentinel'})
        checks['v1_old_request_replay'] = code == 200 and item.get('id') == sentinel['ticket']['id']
    except Exception as exc:
        checks['completed'] = False
        checks['failure_type_' + type(exc).__name__] = False
    finally:
        if proxy is not None:
            proxy.stdin.write('shutdown\n')
            proxy.stdin.flush()
            proxy.stdin.close()
            proxy.wait(timeout=40)
        remote({'op': 'activate', 'release': before['active_release'], 'request_id': 'examiner-restore'})
        for _ in range(20):
            restored = remote({'op': 'status'})
            if restored['readiness']['http_status'] == 200:
                break
            time.sleep(1)
        checks['restored_good_v2'] = restored['active_release'] == before['active_release'] and restored['image_id'] == before['image_id'] and restored['readiness']['http_status'] == 200
        final_db = remote({'op': 'db-audit'})
        checks['schema_stays_v2'] = final_db['schema_version'] == 2 and final_db['integrity'] == 'ok'
        result = {'passed': all(checks.values()), 'checks': checks, 'scope': 'own_label_verified_lab_images_only'}
        output.write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--passed-report', required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(main(args.root, args.passed_report))
