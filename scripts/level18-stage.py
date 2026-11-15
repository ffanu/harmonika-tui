#!/usr/bin/env python3
"""Stage synthetic Level18 exams and tests privately; never restart services."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

TESTS = ('agent-controller agent-initiative agent-progress level4-evidence job-lifecycle '
         'receipt-outcome verification-scope task-continuity command-batch-continuity proof-contract source-reference-firewall '
         'auth-reference-firewall planner-context fullstack-workflow decision-recovery '
         'long-decision interaction-workflow ui-selector-metadata interactive-responsiveness').split()
FILES = ('receipt-outcome.cjs', 'verification-scope.cjs', 'task-continuity.cjs', 'decision-recovery.cjs', 'agent-controller.cjs', 'internal-tool-planner.js')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--stage', required=True)
    args = parser.parse_args()
    args.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'vps']

    def run(command, label, **kwargs):
        result = subprocess.run(command, capture_output=True, text=True, **kwargs)
        output = args.root/(label+'.txt')
        output.write_text(result.stdout+result.stderr)
        output.chmod(0o600)
        if result.returncode:
            raise RuntimeError(label+'_failed')
        print(label+'_passed', flush=True)
        return result.stdout

    run(['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_level18_activation.py'], 'activation-guards')
    testpaths = ['tests/'+name+'.test.cjs' for name in TESTS]
    run(['node', '--test', *testpaths], 'local-tests')
    paths = [*Path('backend/planner').glob('*.cjs'), *Path('backend/planner').glob('*.js'),
             Path('backend/planner/context-capacity.json'), Path('backend/planner/python-reference-spans.py'),
             *map(Path, testpaths), Path('tests/decision-planner-integration.test.cjs'),
             Path('scripts/activate-debug-workflow.py'), *Path('scripts').glob('level18-*.cjs'),
             Path('scripts/level18-activate.py')]
    payload = {'root': args.stage, 'files': {str(p): base64.b64encode(p.read_bytes()).decode() for p in paths},
               'plan': json.loads(Path('docs/LEVEL18-EXAM-PLAN-20261115.json').read_text()),
               'names': FILES}
    stagecode = '''import json,sys,pathlib,base64,hashlib,shutil
v=json.load(sys.stdin);root=pathlib.Path(v['root']);root.mkdir(mode=0o700,exist_ok=False)
for name,encoded in v['files'].items():
 p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(encoded));p.chmod(0o600)
(root/'backend/planner/internal-tool-deps').symlink_to('/opt/harmonika-ai/api/internal-tool-deps',target_is_directory=True)
(root/'plan.json').write_text(json.dumps(v['plan']));(root/'plan.json').chmod(0o600)
base=pathlib.Path('/opt/harmonika-ai/api');expected={n:hashlib.sha256((base/n).read_bytes()).hexdigest() if (base/n).is_file() else None for n in v['names']}
dependencies={}
for n in ('remote-workflow.cjs','remote-workflow.txt'):
 target=root/'backend/planner'/n
 if not target.exists():shutil.copy2(base/n,target);target.chmod(0o600)
 dependencies[n]=hashlib.sha256(target.read_bytes()).hexdigest()
(root/'stage-dependencies.json').write_text(json.dumps(dependencies));(root/'stage-dependencies.json').chmod(0o600)
(root/'expected-baseline.json').write_text(json.dumps(expected));(root/'expected-baseline.json').chmod(0o600)
'''
    run([*ssh, 'python3 -c '+shlex.quote(stagecode)], 'stage', input=json.dumps(payload))
    linux = 'cd '+shlex.quote(args.stage)+' && HARMONIKA_TEST_PLANNER_SOURCE='+shlex.quote(args.stage+'/backend/planner/internal-tool-planner.js')+' HARMONIKA_TEST_PLANNER_BASE=/opt/harmonika-ai/api/internal-tool-planner.js node --test '+' '.join(map(shlex.quote, [*testpaths, 'tests/decision-planner-integration.test.cjs']))
    run([*ssh, linux], 'linux-tests')
    manifest = {'candidate_sha256': {n: hashlib.sha256((Path('backend/planner')/n).read_bytes()).hexdigest() for n in FILES},
                'local_passed': True, 'integration_passed': True, 'activation_guards_passed': True}
    writecode = 'import pathlib,sys;p=pathlib.Path('+repr(args.stage+'/verification.json')+');p.write_text(sys.stdin.read());p.chmod(0o600)'
    run([*ssh, 'python3 -c '+shlex.quote(writecode)], 'verification', input=json.dumps(manifest))
    (args.root/'stage.txt').write_text(args.stage)
    (args.root/'stage.txt').chmod(0o600)


if __name__ == '__main__':
    main()
