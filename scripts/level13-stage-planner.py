#!/usr/bin/env python3
"""Stage and verify Level13 only; activation uses level13-activate-local.py."""
import argparse,base64,hashlib,json,shlex,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--stage',required=True);p.add_argument('--label',required=True);p.add_argument('--verify-only',action='store_true',help='Stage and verify without activating or restarting the gateway');a=p.parse_args();r=a.root;stage=a.stage
names=['proof-contract','level4-evidence','source-reference-firewall','auth-reference-firewall','planner-context','fullstack-workflow','decision-recovery','agent-controller','agent-progress','job-lifecycle','long-decision','interaction-workflow','ui-selector-metadata']
guard=subprocess.run(['python3','-m','unittest','discover','-s','tests','-p','test_debug_workflow_activation.py','-v'],capture_output=True,text=True);(r/(a.label+'-activation-guards.txt')).write_text(guard.stdout+guard.stderr);assert guard.returncode==0
proof_guard=subprocess.run(['python3','-m','unittest','discover','-s','tests','-p','test_level13_activation.py','-v'],capture_output=True,text=True);(r/(a.label+'-proof-activation-guards.txt')).write_text(proof_guard.stdout+proof_guard.stderr);assert proof_guard.returncode==0
x=subprocess.run(['node','--test',*['tests/'+n+'.test.cjs' for n in names]],capture_output=True,text=True);(r/(a.label+'-local-tests.txt')).write_text(x.stdout+x.stderr);assert x.returncode==0;print('local_passed',flush=True)
paths=[Path('tests/proof-contract.test.cjs'),Path('tests/ui-selector-metadata.test.cjs')]+list(Path('backend/planner').glob('*.cjs'))+[Path('backend/planner/planner-context.js'),Path('backend/planner/context-capacity.json'),Path('backend/planner/python-reference-spans.py'),Path('backend/planner/internal-tool-planner.js')]+[Path(n) for n in ['tests/decision-planner-integration.test.cjs','tests/long-decision.test.cjs','tests/decision-recovery.test.cjs','tests/source-reference-firewall.test.cjs','tests/auth-reference-firewall.test.cjs','tests/level4-evidence.test.cjs','scripts/activate-debug-workflow.py','scripts/activate-source-reference.py','scripts/source-reference-smoke.cjs','scripts/queue-decision-smoke.cjs']]
code="""from pathlib import Path
import sys,json,base64,os,shutil
v=json.load(sys.stdin);root=Path(v['root']);root.mkdir(mode=0o700,exist_ok=True)
for n,d in v['files'].items():
 p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(d));os.chmod(p,0o600)
shutil.copy2(root/'backend/planner/internal-tool-planner.js',root/'planner-under-test.js')
"""
x=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'root':stage,'files':{str(n):base64.b64encode(n.read_bytes()).decode() for n in paths}}),capture_output=True,text=True);assert x.returncode==0
x=subprocess.run(['ssh','-o','BatchMode=yes','vps','cd '+shlex.quote(stage)+' && HARMONIKA_TEST_PLANNER_SOURCE='+shlex.quote(stage+'/planner-under-test.js')+' HARMONIKA_TEST_PLANNER_BASE=/opt/harmonika-ai/api/internal-tool-planner.js node --test tests/proof-contract.test.cjs tests/decision-planner-integration.test.cjs tests/source-reference-firewall.test.cjs tests/auth-reference-firewall.test.cjs tests/level4-evidence.test.cjs tests/long-decision.test.cjs tests/decision-recovery.test.cjs tests/ui-selector-metadata.test.cjs'],capture_output=True,text=True);(r/(a.label+'-linux-tests.txt')).write_text(x.stdout+x.stderr);assert x.returncode==0;print('linux_passed',flush=True)
# Security hardening: all positive fixtures already pass in the previous build.
v={'candidate_sha256':{n:hashlib.sha256(Path('backend/planner',n).read_bytes()).hexdigest() for n in ['proof-contract.cjs','python-reference-spans.py','secret-firewall.cjs','planner-context.js','agent-controller.cjs','long-decision.cjs','internal-tool-planner.js','debug-workflow.cjs','decision-recovery.cjs']},'local_passed':True,'integration_passed':True,'activation_guards_passed':True,'level13_activation_guards_passed':True}
code="from pathlib import Path;import sys,os;p=Path("+repr(stage+'/verification.json')+");p.write_text(sys.stdin.read());os.chmod(p,0o600)"
x=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps(v),capture_output=True,text=True);assert x.returncode==0
print('verified_staged_no_activation',flush=True)
