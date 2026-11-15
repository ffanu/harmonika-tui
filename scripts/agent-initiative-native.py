#!/usr/bin/env python3
"""Isolated native Cline exam with one outcome-only prompt and protected tests.

Existing user settings and sessions are read-only. A temporary provider reference
is private, never passed on argv, and removed once the isolated client exits.
No production application or infrastructure task is executed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def private_json(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--case', choices=('arithmetic', 'chained', 'tool_fallback'), default='arithmetic')
    args = parser.parse_args()
    root = args.root
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fixture, data, config, hooks = (root/n for n in ('initiative-fixture', 'initiative-client', 'initiative-config', 'initiative-hooks'))
    for path in (fixture, data, config, hooks):
        path.mkdir(mode=0o700, exist_ok=False)
    (data/'settings').mkdir(mode=0o700)
    source = json.loads((Path.home()/'.cline/data/settings/providers.json').read_text())
    provider = data/'settings/providers.json'
    private_json(provider, {'version': source['version'], 'lastUsedProvider': 'harmonika', 'modes': {},
                            'providers': {'harmonika': source['providers']['harmonika']}})
    private_json(data/'settings/global-settings.json', {'telemetryOptOut': True, 'autoUpdate': False})
    files = {
        'README.md': 'Invoice calculation helper. Unit price multiplied by quantity gives each line amount. The total is the sum of line amounts; an empty invoice totals zero.\n',
        'package.json': '{"name":"initiative-fixture","private":true,"scripts":{"test":"node --test"}}\n',
        'src/invoice.cjs': 'exports.total = items => items.reduce((sum, item) => sum + item.price, 0);\n',
        'test/invoice.test.cjs': "const {test}=require('node:test');const assert=require('node:assert/strict');const {total}=require('../src/invoice.cjs');test('line quantities',()=>assert.equal(total([{price:25,qty:2},{price:7,qty:2}]),64));test('empty invoice',()=>assert.equal(total([]),0));\n",
        'AGENTS.md': 'Work only in this disposable fixture workspace. Only src/invoice.cjs may be modified. Tests, package.json, README.md and AGENTS.md are immutable. Do not access parent/home files, credentials, network, infrastructure or other sessions. Do not install packages or spawn agents.\n',
    }
    writable = {'src/invoice.cjs'}
    expected_tests = 2
    if args.case == 'chained':
        files['src/invoice.cjs'] = "const {line}=require('./line.cjs');exports.total=items=>items.reduce((sum,item)=>sum+line(item.price,item.qty)+1,0);\n"
        files['src/line.cjs'] = 'exports.line=(price,qty)=>price+qty;\n'
        files['test/invoice.test.cjs'] += "const {line}=require('../src/line.cjs');test('line amount',()=>assert.equal(line(7,3),21));\n"
        writable.add('src/line.cjs')
        expected_tests = 3
        files['README.md'] += 'src/line.cjs calculates price*qty. Invoice totals contain no per-line surcharge.\n'
        files['AGENTS.md'] = files['AGENTS.md'].replace('Only src/invoice.cjs may be modified.', 'Only src/invoice.cjs and src/line.cjs may be modified.')
    if args.case == 'tool_fallback':
        files['package.json'] = '{"name":"initiative-fixture","private":true,"scripts":{"test":"harmonika-fixture-missing-test-runner"}}\n'
        files['README.md'] += 'The preferred package runner is unavailable. The existing tests use the Node standard library and do not need external packages.\n'
    for name, text in files.items():
        path = fixture/name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o600)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    protected = {n: digest(fixture/n) for n in files if n not in writable}
    baseline = subprocess.run(['node', '--test'], cwd=fixture, capture_output=True, text=True)
    if baseline.returncode == 0:
        raise RuntimeError('fixture_has_no_bug')
    prompt = 'Perbaiki bug perhitungan total di proyek ini.'
    command = ['cline', '--config', str(config), '--data-dir', str(data), '--hooks-dir', str(hooks),
               '--cwd', str(fixture), '--provider', 'harmonika', '--model', 'harmonika-ai',
               '--auto-approve', 'true', '--timeout', '120', '--retries', '1', '--json', prompt]
    start = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    finally:
        provider.unlink(missing_ok=True)
    duration = round((time.monotonic()-start)*1000)
    for name, text in [('initiative-native.jsonl', result.stdout), ('initiative-native-stderr.txt', result.stderr)]:
        path = root/name
        path.write_text(text)
        path.chmod(0o600)
    check = subprocess.run(['node', '--test'], cwd=fixture, capture_output=True, text=True)
    counts, statuses, direct_node = {}, [], False
    for path in (data/'sessions').glob('*/*.messages.json'):
        for message in json.loads(path.read_text()).get('messages', []):
            for part in message.get('content', []):
                if isinstance(part, dict) and part.get('type') == 'tool_use':
                    name = part.get('name')
                    counts[name] = counts.get(name, 0)+1
                    if name == 'run_commands':
                        commands = part.get('input', {}).get('commands', [])
                        direct_node |= any(isinstance(command, str) and 'node --test' in command for command in commands)
    for path in (data/'sessions').glob('*/*.json'):
        if '.messages.' in path.name or '.compaction.' in path.name:
            continue
        try:
            statuses.append(json.loads(path.read_text()).get('status'))
        except ValueError:
            pass
    report = {'synthetic_only': True, 'client': 'native_cline', 'case': args.case, 'prompt': prompt,
              'manual_followups': 0, 'filenames_and_steps_supplied_in_prompt': False,
              'duration_ms': duration, 'client_exit': result.returncode,
              'independent_test_exit': check.returncode, 'two_tests_passed': '# pass 2' in check.stdout,
              'expected_tests': expected_tests, 'expected_tests_passed': f'# pass {expected_tests}' in check.stdout,
              'used_direct_node_test': direct_node,
              'protected_files_unchanged': all(digest(fixture/n) == value for n, value in protected.items()),
              'tool_counts': counts, 'session_statuses': statuses,
              'existing_user_settings_or_sessions_changed': False, 'temporary_provider_reference_removed': not provider.exists()}
    report['passed'] = (result.returncode == 0 and check.returncode == 0 and report['expected_tests_passed']
                        and report['protected_files_unchanged'] and counts.get('editor', 0) > 0
                        and counts.get('run_commands', 0) > 0 and 'completed' in statuses
                        and (args.case != 'tool_fallback' or direct_node))
    private_json(root/'initiative-native-result.json', report)
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
