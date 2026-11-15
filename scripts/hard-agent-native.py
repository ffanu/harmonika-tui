#!/usr/bin/env python3
"""Run the installed branded Harmonika terminal in a private standalone profile."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

BINARY=Path('/Users/id/Projects/harmonika-engine/bin/harmonika')


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)


def environment(root,work):
    for n in ('config','data','cache','state','xdg-config'):(root/n).mkdir(mode=0o700,parents=True,exist_ok=True)
    config={'model':'harmonika/harmonika-ai','provider':{'harmonika':{'npm':'@ai-sdk/openai-compatible',
        'options':{'baseURL':'https://chat.harmonika.id/v1','apiKey':'{env:HARMONIKA_API_KEY}'},
        'models':{'harmonika-ai':{'name':'Harmonika Ai','limit':{'context':872000,'output':4096}}}}},
        'permission':{'*':'ask','read':'allow','edit':'allow','bash':'allow','external_directory':'deny'}}
    save(root/'config/opencode.json',config)
    env={k:v for k,v in os.environ.items() if not k.startswith(('OPENCODE_','HARMONIKA_'))
         and not re.search(r'api.?key|token|secret|password',k,re.I)}
    env.update({'PWD':str(work.resolve()),'OPENCODE_CONFIG_DIR':str(root/'config'),'OPENCODE_CONFIG':str(root/'config/opencode.json'),
        'OPENCODE_DB':str(root/'data/opencode.db'),'OPENCODE_DISABLE_PROJECT_CONFIG':'1',
        'OPENCODE_DISABLE_MODELS_FETCH':'1','OPENCODE_DISABLE_FILEWATCHER':'1','OPENCODE_DISABLE_CLAUDE_CODE':'1',
        'XDG_CONFIG_HOME':str(root/'xdg-config'),'XDG_DATA_HOME':str(root/'data'),
        'XDG_CACHE_HOME':str(root/'cache'),'XDG_STATE_HOME':str(root/'state')})
    env['HARMONIKA_API_KEY']=(Path.home()/'.config/harmonika/harmonika-api-key').read_text().strip()
    return env


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--case',choices=('calibration','service_recovery','evidence_audit'),required=True)
    p.add_argument('--prepare-only',action='store_true');a=p.parse_args()
    a.root.mkdir(mode=0o700,parents=True,exist_ok=False)
    if a.case=='calibration':
        prep=subprocess.run(['python3',str(Path(__file__).with_name('initiative-command-native.py')),
            '--root',str(a.root/'setup'),'--case','arithmetic','--prepare-only'],capture_output=True,text=True,stdin=subprocess.DEVNULL)
        if prep.returncode:raise RuntimeError('calibration_prepare_failed')
        work=a.root/'setup/initiative-fixture';prompt='Perbaiki bug perhitungan total di proyek ini.'
        protected={n:hashlib.sha256((work/n).read_bytes()).hexdigest() for n in ('README.md','AGENTS.md','package.json','test/invoice.test.cjs')}
    else:
        spec=importlib.util.spec_from_file_location('hard_fixture',Path(__file__).with_name('hard_agent_fixtures.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        work,prompt,protected=module.prepare(a.root,a.case)
    save(a.root/'input-plan.json',{'case':a.case,'prompt':prompt,'protected_sha256':protected,
        'client':'installed_harmonika_binary','binary_sha256':hashlib.sha256(BINARY.read_bytes()).hexdigest(),
        'standalone':True,'stdin_isolated':True,'manual_followups':0,'existing_session_used':False})
    if a.prepare_only:print(json.dumps({'case':a.case,'prepared':True,'provider_accessed':False}));return 0
    env=environment(a.root/'profile',work);start=time.monotonic()
    with (a.root/'stdout.jsonl').open('w') as out,(a.root/'stderr.txt').open('w') as err:
        (a.root/'stdout.jsonl').chmod(0o600);(a.root/'stderr.txt').chmod(0o600)
        proc=subprocess.Popen([str(BINARY),'run','--standalone','--auto','--format','json',
            '--model','harmonika/harmonika-ai',prompt],cwd=work,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=err)
        save(a.root/'client-process.json',{'pid':proc.pid,'parent_pid':os.getpid(),
            'owned_by_runner':True,'workspace':str(work.resolve()),'standalone':True})
        deadline_exceeded=False
        try:rc=proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            deadline_exceeded=True
            # Only the exact Harmonika client started above, never user sessions
            # or protected Codex/OpenCode processes. Let its own server clean up.
            proc.send_signal(signal.SIGINT)
            rc=proc.wait()
    rows=[];final=[];errors=[]
    for line in (a.root/'stdout.jsonl').read_text().splitlines():
        try:v=json.loads(line)
        except ValueError:continue
        part=v.get('part',{})
        if v.get('type')=='text':final.append(part.get('text',''))
        if v.get('type')=='tool_use' or part.get('type')=='tool':
            rows.append({'tool':part.get('tool'),'status':part.get('state',{}).get('status')})
        if v.get('type')=='error':errors.append(v.get('error',{}).get('name','error'))
    save(a.root/'final-response-private.json',{'synthetic_only':True,'text':'\n'.join(final[-1:])})
    result={'case':a.case,'client':'installed_harmonika_binary','standalone':True,'client_exit':rc,
        'duration_ms':round((time.monotonic()-start)*1000),'tool_events':rows,'error_names':errors,
        'deadline_exceeded':deadline_exceeded,
        'protected_files_unchanged':all(hashlib.sha256((work/n).read_bytes()).hexdigest()==h for n,h in protected.items()),
        'manual_followups':0,'key_written_to_config':False,'existing_user_sessions_or_settings_changed':False}
    if a.case=='calibration':
        check=subprocess.run(['node','--test'],cwd=work,capture_output=True,text=True,stdin=subprocess.DEVNULL)
        result.update(independent_test_exit=check.returncode,expected_tests_passed='# pass 2' in check.stdout)
        result['passed']=rc==0 and not errors and result['protected_files_unchanged'] and check.returncode==0 and result['expected_tests_passed']
    else:result.update(module.grade(a.root,a.case,work,rc,errors,result['protected_files_unchanged']))
    save(a.root/'result.json',result);print(json.dumps(result,ensure_ascii=False))
    if result.get('semantic_review_required'):return 0 if result['mechanical_checks_passed'] else 1
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
