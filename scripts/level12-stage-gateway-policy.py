"""Read-only source snapshot + isolated Go tests/build; does not activate service."""
import base64,hashlib,json,shlex,subprocess
from pathlib import Path
REPO=Path(__file__).resolve().parents[1];R=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());lab=json.loads((R/'lab.json').read_text());stage='/opt/harmonika-ai/level12-policy-'+lab['lab_id'];payload={'stage':stage,'files':{name:base64.b64encode((REPO/'backend/planner'/name).read_bytes()).decode() for name in ['public-tool-policy-errors.go','public-tool-policy-errors_test.go','public-tool-policy-integration_test.go']},'patch':base64.b64encode((REPO/'scripts/patch-public-tool-policy.py').read_bytes()).decode()}
code=r'''import base64,hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
r=json.load(sys.stdin);base=Path('/srv/harmonika-web-chat/current').resolve();stage=Path(r['stage']);stage.mkdir(mode=0o700,exist_ok=False);source=stage/'source';source.mkdir(mode=0o700)
files=list(base.glob('*.go'))+[base/'go.mod',base/'go.sum'];baseline={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()};baseline['harmonika-web']=hashlib.sha256((base/'harmonika-web').read_bytes()).hexdigest();(stage/'baseline.json').write_text(json.dumps({'source':str(base),'hashes':baseline},indent=2))
for p in files:
 if p.is_file():shutil.copy2(p,source/p.name)
for name,data in r['files'].items():
 if (source/name).exists():raise RuntimeError('policy_file_collision')
 (source/name).write_bytes(base64.b64decode(data))
patch=stage/'patch.py';patch.write_bytes(base64.b64decode(r['patch']));p=subprocess.run(['python3',str(patch),str(source)],capture_output=True,text=True,check=True);(stage/'patch-result.json').write_text(p.stdout)
env=dict(os.environ);env['GOPROXY']='off';env['GOTOOLCHAIN']='local';env['PWD']=str(source);go='/usr/local/bin/go'
p=subprocess.run([go,'fmt','./...'],cwd=source,env=env,capture_output=True,text=True,timeout=60);(stage/'fmt.output').write_text(p.stdout+p.stderr)
p=subprocess.run([go,'test','-count=1','-run','^TestLevel12ToolPolicy','./...'],cwd=source,env=env,capture_output=True,text=True,timeout=180);(stage/'tests.output').write_text(p.stdout+p.stderr);tests=p.returncode==0
if not tests:print(json.dumps({'stage':str(stage),'tests_passed':False,'exit_code':p.returncode}));sys.exit(1)
p=subprocess.run([go,'build','-o',str(stage/'harmonika-web'),'.'],cwd=source,env=env,capture_output=True,text=True,timeout=180);(stage/'build.output').write_text(p.stdout+p.stderr);build=p.returncode==0
proof={'stage':str(stage),'tests_passed':tests,'build_passed':build,'original_source':str(base),'baseline':baseline,'candidate_source_sha256':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in ['public_ai_internal.go',*r['files']]},'binary_sha256':hashlib.sha256((stage/'harmonika-web').read_bytes()).hexdigest() if build else None,'active_service_changed':False,'at':time.time()};(stage/'proof.json').write_text(json.dumps(proof,indent=2));print(json.dumps(proof));sys.exit(not build)
'''
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps(payload),capture_output=True,text=True,timeout=450);R.joinpath('gateway-policy-stage-result.json').write_text(p.stdout);R.joinpath('gateway-policy-stage.stderr').write_text(p.stderr);print(p.stdout);print(json.dumps({'exit_code':p.returncode}));raise SystemExit(p.returncode)
