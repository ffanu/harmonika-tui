"""Read-only engineering evidence worker, independent of model provider."""
import json
from pathlib import Path
import resource
import sys

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
BASE = TOOLS.parent.parent if TOOLS.parent.name == 'runtime' else TOOLS.parent
sys.path.insert(0, str(BASE/'skills/harmonika-engineering/scripts'))
from skill_registry import Registry
from engineering_analyzers import analyze

OPERATIONS = frozenset('engineering.'+x for x in ('code_review','debug','testing_strategy','documentation'))


def capabilities():
    return {'schema_version':1,'operations':{
        name:{'status':'available','reason':None,'mode':'supplied_evidence_only',
              'code_execution':False,'repository_connector':False}
        for name in sorted(OPERATIONS)}}


def main():
    try:
        if sys.argv[1:] == ['--capabilities']:
            print(json.dumps(capabilities()));return 0
        resource.setrlimit(resource.RLIMIT_CPU,(8,10))
        if sys.platform.startswith('linux'):
            resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        raw=sys.stdin.buffer.read(262145)
        if len(raw)>262144:raise ValueError('input_too_large')
        body=json.loads(raw);name=body['skill_id'];payload=body['input']
        if name not in OPERATIONS:raise ValueError('skill_not_allowed')
        Registry().validate_input(name,payload)
        result=analyze(name.split('.',1)[1],payload)
        if isinstance(result,dict):result['operation']=name
        if not isinstance(result,dict) or result.get('operation')!=name:
            raise ValueError('invalid_engineering_result')
        result['content_is_untrusted']=True
        encoded=json.dumps({'ok':True,'result':result},ensure_ascii=False,allow_nan=False)
        if len(encoded.encode())>65536:raise ValueError('engineering_output_too_large')
        sys.stdout.write(encoded);return 0
    except Exception:
        sys.stderr.write('{"code":"engineering_worker_failed"}');return 2

if __name__=='__main__':sys.exit(main())
