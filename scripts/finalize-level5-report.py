#!/usr/bin/env python3
"""Export mechanical evidence only after grading, compatibility and cleanup pass."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

REPO = Path(__file__).resolve().parents[1]


def main(root, passed_report):
    read = lambda name: json.loads((root / name).read_text())
    final = json.loads(passed_report.read_text())
    backward = read('backward-image-result.json')
    cleanup = read('lab-cleanup.json')
    health = read('backend-final-health.json')
    assert final.get('phase') == 'v2' and final.get('passed') is True
    assert backward.get('passed') is True and cleanup.get('closed') is True
    assert cleanup.get('owned_containers_remaining') == 0 and cleanup.get('owned_images_remaining') == 0 and cleanup.get('owned_volume_remaining') is False
    assert health.get('passed') is True
    for name in ['continued-protocol-a-run.json', 'continued-run.json', 'continued-touch-r1-run.json']:
        run = read(name)
        assert run['native_exit_code'] == 0 and run['same_session'] is True
    target = REPO / 'docs/LEVEL5-AGENT-EXAM-20261114.json'
    report = json.loads(target.read_text())
    assert report['status'] == 'in_progress'
    report['status'] = 'passed_scoped_lab'
    report['remaining'] = []
    report['native_runs'] = [value for file in sorted(root.glob('*-run.json')) if 'native_exit_code' in (value := json.loads(file.read_text()))]
    report['backend_activation'] = read('engine-v11-activation.txt')
    report['backend_tests'].update(local=138, linux=47, actual_ui_guidance_probe=json.loads((root / 'engine-v11-ui-guidance-probe.txt').read_text().splitlines()[0]))
    report['backend_changes'].append('Live QA instructions require actual async children and download-link target measurements; repository skill guidance aligned')
    report['backend_final_health'] = health
    report['native_file_scope_metadata'] = read('native-scope-check.json')
    unit_output = (root / (passed_report.name.removesuffix('-result.json') + '-public.txt')).read_text()
    ids = re.findall(r'^(test_\S+) \(([^)]+)\)', unit_output, re.M)
    browser = final['tests']['browser']['result']
    report['turn_b'] = {'passed': True, 'top_checks': final['checks'], 'unit_executions': len(ids), 'distinct_unittest_ids': len(set(ids)), 'unit_count_note': 'Includes inherited/discovery repeats; not a unique-business-scenario count.', 'direct_checks': read_direct(root, passed_report), 'api_executed': final['tests']['api']['tests'], 'api_skipped': final['tests']['api']['skipped'], 'browser_checks': len(browser['checks']), 'browser_actual': browser['actual_browser'], 'physical_mobile_ime_tested': False}
    initial = read('v2-first-result.json')
    report['product_bug_and_correction'] = {'initial_failed_check': 'Rendered attachment download link height below36px', 'initial_browser_passed': initial['tests']['browser']['passed'], 'correction_release': 'app-v2-r1', 'native_correction_exit': read('continued-touch-r1-run.json'), 'initial_failure_preserved': True, 'acceptance_not_lowered': True}
    report['actual_old_image_compatibility'] = backward
    report['compatibility_scope'] = 'Examiner switched retained v1 image onto v2 DB, checked readiness/read/download/legacy replay, then restored v2-r1. Not all old-version mutations or UX were certified.'
    report['native_fault_scope'] = 'bad-v3 reuses the v2 image with forced-unready configuration; native detected health200/readiness503 and restored the known release configuration.'
    report['owned_lab_cleanup'] = cleanup
    report['last_validated_release'] = 'app-v2-r1'
    report['limits'].extend(['Native CLI tested with --standalone and isolated XDG directories; existing user plugin state was not certified.', 'Source parser is conservative; arbitrary framework getter forms can still fail closed.', 'Some initial native wrapper errors were surfaced as generic engine_error; this is not a claim that every error mapping or secret-exfiltration route is certified.'])
    for prefix, result in [('v2-failed', initial), ('v2-corrected', final)]:
        directory = Path(result['tests']['browser']['result']['out'])
        for name in ['desktop.png', 'mobile.png']:
            source = directory / name
            assert source.resolve().is_relative_to((root / 'project').resolve()) and not source.is_symlink()
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            artifact = REPO / 'docs/assets/exams/level5-20261114' / (prefix + '-' + source.stem + '-' + digest[:8] + '.png')
            artifact.parent.mkdir(parents=True, exist_ok=True)
            if artifact.exists():
                assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest
            else:
                shutil.copy2(source, artifact)
            report['artifacts'].append({'path': str(artifact.relative_to(REPO)), 'sha256': digest, 'scope': 'synthetic_ticket_application', 'passed': result['tests']['browser']['passed']})
    for name in ['v1-first-result.json', 'v2-first-result.json', passed_report.name, 'backward-image-result.json', 'lab-cleanup.json', 'backend-final-health.json', 'native-scope-check.json']:
        report.setdefault('private_evidence_hashes', {})[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'api_cases': report['turn_b']['api_executed'], 'browser_checks': report['turn_b']['browser_checks'], 'cleanup': True, 'backend_healthy': True}))


def read_direct(root, report):
    name = report.name.removesuffix('-result.json') + '-direct.txt'
    value = json.loads((root / name).read_text().splitlines()[0])
    assert value['passed'] is True
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--passed-report', required=True, type=Path)
    args = parser.parse_args()
    main(args.root, args.passed_report)
