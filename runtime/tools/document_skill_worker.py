"""Internal subprocess adapter: shared contract -> existing document handlers."""
import sys
from pathlib import Path
import json
import os
import resource
import tempfile

resource.setrlimit(resource.RLIMIT_CPU,(20,20))
if sys.platform.startswith('linux'):resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
TOOLS=Path(__file__).resolve().parent
sys.path.insert(0,str(TOOLS))
BASE=TOOLS.parent.parent if TOOLS.parent.name=='runtime' else TOOLS.parent
sys.path.insert(0,str(BASE/'skills/harmonika-documents/scripts'))
from skill_registry import Registry
import documents

if __name__=='__main__':
    try:
        if sys.argv[1:]==['--capabilities']:
            print(json.dumps(documents.detailed_capabilities()))
            sys.exit(0)
        raw=sys.stdin.buffer.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('input_too_large')
        body=json.loads(raw);registry=Registry();skill=body['skill_id'];payload=body['input']
        registry.validate_input(skill,payload);action,extension=registry.route(skill)
        root=body['project_root']
        for field in ('path','output_path'):
            if field in payload:
                file=documents.scoped(root,payload[field])
                if file.suffix.lower()!=extension:raise ValueError('invalid_format')
        if action=='edit':
            for ref in payload['changes'].get('append_files',[]):documents.scoped(root,ref)
        # ReportLab must not execute owner dot-config; paths were validated first.
        with tempfile.TemporaryDirectory(prefix='harmonika-skill-worker-') as home:
            os.environ['HOME']=home
            if action=='inspect':result=documents.inspect(Path(payload['path']))
            elif action=='create':result=documents.create(Path(payload['output_path']),payload['content'])
            elif action=='edit':result=documents.edit(Path(payload['path']),Path(payload['output_path']),payload['changes'],root)
            else:raise ValueError('unsupported_operation')
            encoder=json.JSONEncoder(ensure_ascii=False,allow_nan=False,default=str)
            for piece in encoder.iterencode({'ok':True,'result':result}):sys.stdout.write(piece)
    except Exception as error:
        # No payload, document text, file path or raw parser traceback in stderr.
        prefix=str(error).split(':',1)[0]
        allowed={'unsupported_glyphs','pdf_needs_ocr','pdf_encrypted','invalid_pdf','pdf_page_limit','file_too_large','invalid_spec','output_too_large','unsupported_operation','unsupported_format'}
        code='missing_dependency' if str(error).startswith('Unsupported: missing optional') else (prefix if prefix in allowed else 'document_worker_failed')
        sys.stderr.write(json.dumps({'code':code}))
        sys.exit(2)
