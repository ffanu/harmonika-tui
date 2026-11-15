#!/usr/bin/env python3
"""Exercise actual Harmonika CLI in a fresh synthetic project, never a user session."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

FIXTURES = Path(__file__).resolve().parents[1]/'tests/fixtures/native-autonomy.json'
RULES = ('Work only inside this synthetic workspace. Do not use network, SSH, browser, '
         'media, credential files or external agents. Read/edit the JavaScript fixture '
         'and run node --test. Never modify test assertions to hide a bug. Finish the '
         'requested implementation and verify it before reporting.\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['easy', 'medium', 'hard'], required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    fixture = json.loads(FIXTURES.read_text())[args.case]
    stage = Path(tempfile.mkdtemp(prefix='harmonika-native-autonomy-'))
    stage.chmod(0o700)
    project = stage/args.case
    project.mkdir(mode=0o700)
    (project/'AGENTS.md').write_text(RULES)
    (project/fixture['source_file']).write_text(fixture['source'])
    test = project/fixture['test_file']
    test.write_text(fixture['test'])
    before = hashlib.sha256(test.read_bytes()).hexdigest()
    if args.prepare_only:
        print(json.dumps({'case': args.case, 'stage': str(stage), 'prepared_only': True}))
        return
    env = os.environ.copy()
    for name in ['XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME']:
        folder = stage/name.lower()
        folder.mkdir(mode=0o700)
        env[name] = str(folder)
    log = stage/'native.jsonl'
    started = time.monotonic()
    with log.open('w') as stream:
        log.chmod(0o600)
        process = subprocess.Popen(['harmonika', 'run', '--standalone', '--auto', '--format', 'json',
                                    '--model', 'harmonika/harmonika-ai', fixture['prompt']+
                                    ' Kerjakan sampai selesai dalam folder ini, tanpa jaringan, credentials, proyek atau agent lain.'],
                                   cwd=project, env=env, stdout=stream, stderr=subprocess.STDOUT)
        print(json.dumps({'case': args.case, 'native_pid': process.pid, 'stage': str(stage), 'isolated_data_dir': True}), flush=True)
        # Never kill another process or replay a tool after an uncertain result.
        code = process.wait()
    verified = subprocess.run(['node', '--test'], cwd=project, capture_output=True, text=True)
    counts = {name: int(match.group(1)) if (match := re.search(r'^# '+name+r' (\d+)$', verified.stdout, re.M)) else None
              for name in ['tests', 'pass', 'fail']}
    report = {'case': args.case, 'stage': str(stage), 'native_exit_code': code,
              'external_test_exit_code': verified.returncode,
              'tests_unchanged': hashlib.sha256(test.read_bytes()).hexdigest() == before,
              'result_file_created': (project/'HASIL.md').is_file(), 'test_counts': counts,
              'duration_seconds': round(time.monotonic()-started, 1),
              'existing_user_session_reused': False, 'provider_configuration_changed': False}
    report['passed'] = code == 0 and verified.returncode == 0 and report['tests_unchanged'] and report['result_file_created']
    (stage/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
