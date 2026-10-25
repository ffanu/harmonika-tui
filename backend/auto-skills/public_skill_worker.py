"""Fixed public read-only boundary; installed alongside private runtime/tools."""
import json
import os
from pathlib import Path
import re
import runpy
import sys
import io
import resource

ALLOWED = frozenset('seo.' + name for name in (
    'page_audit', 'technical', 'content', 'schema', 'sitemap', 'performance',
    'local', 'keyword_cluster', 'geo', 'backlinks', 'search_console', 'ga4')) | frozenset('engineering.'+name for name in ('code_review','debug','testing_strategy','documentation'))


def validate(body):
    if type(body) is not dict or set(body) != {'skill_id', 'input', 'principal_id'}:
        raise ValueError('invalid_request')
    if body['skill_id'] not in ALLOWED:
        raise ValueError('skill_not_allowed')
    if not isinstance(body['principal_id'], str) or not re.fullmatch(r'api-token:[1-9][0-9]{0,18}', body['principal_id']):
        raise ValueError('invalid_principal')
    if type(body['input']) is not dict:
        raise ValueError('invalid_input')
    return body


def main():
    try:
        raw = sys.stdin.buffer.read(262145)
        if len(raw) > 262144:
            raise ValueError('input_too_large')
        body = json.loads(raw)
        if type(body) is dict and body.get('action')=='prepare_conversation':
            resource.setrlimit(resource.RLIMIT_CPU,(8,10))
            if sys.platform.startswith('linux'):
                resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
            if set(body)!={'action','messages','principal_id'}:
                raise ValueError('invalid_request')
            validate({'skill_id':'engineering.code_review','input':{},'principal_id':body['principal_id']})
            sys.path.insert(0,str(Path(__file__).resolve().parent))
            from engineering_evidence import from_conversation
            result=from_conversation(body['messages'])
            encoded=json.dumps({'ok':True,'result':result},ensure_ascii=False,allow_nan=False)
            if len(encoded.encode())>524288:raise ValueError('output_too_large')
            sys.stdout.write(encoded)
            return 0
        body = validate(body)
        # Credentials are never inherited by public account adapters. A later
        # authorized connector broker must explicitly bind a token to a property.
        os.environ.pop('HARMONIKA_SEO_GOOGLE_CONFIG', None)
        body['project_root'] = '/var/lib/harmonika-public-skills'
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(body).encode()), encoding='utf-8')
        worker='engineering_skill_worker.py' if body['skill_id'].startswith('engineering.') else 'seo_skill_worker.py'
        sys.argv = [str(Path(__file__).with_name(worker))]
        runpy.run_path(sys.argv[0], run_name='__main__')
    except (ValueError, TypeError, KeyError):
        sys.stderr.write('{"code":"invalid_public_skill_request"}')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
