#!/usr/bin/env python3
"""Send one synthetic context fixture to the owner-authenticated Harmonika API; no retries."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


def main(directory):
    meta=json.loads((directory/'fixture.private.json').read_text());prompt=(directory/'prompt.txt').read_text()
    assert meta['synthetic_only'] is True and hashlib.sha256(prompt.encode()).hexdigest()==meta['prompt_sha256']
    key=(Path.home()/'.config/harmonika/harmonika-api-key').read_text().strip()
    headers={'Content-Type':'application/json','Authorization':'Bearer '+key}
    config=json.loads((Path.home()/'.config/harmonika/harmonika.json').read_text())
    headers.update({k:v for k,v in config['providers']['harmonika'].get('extra_headers',{}).items() if k.lower() in ['x-harmonika-device-id','x-harmonika-device-name']})
    session='context-load-e2e-'+uuid.uuid4().hex
    data={'model':'harmonika-ai','messages':[{'role':'user','content':prompt}],'session_id':session,'stream':False,'max_tokens':256,'tools':[{'type':'function','function':{'name':'synthetic_noop','description':'Never call this synthetic fixture function.','parameters':{'type':'object','properties':{},'additionalProperties':False}}}],'tool_choice':'none'}
    encoded=json.dumps(data).encode();started=time.monotonic()
    result={'synthetic_only':True,'local_text_tokens':meta['local_text_tokens'],'session_id':session,'transport':'production-domain','payload_bytes':len(encoded),'prompt_sha256':meta['prompt_sha256'],'attempts':1,'production_profile_modified':False}
    try:
        with urllib.request.urlopen(urllib.request.Request('https://chat.harmonika.id/v1/chat/completions',data=encoded,headers=headers),timeout=120) as response:
            body=json.loads(response.read(1048576));result['http_status']=response.status
        message=body.get('choices',[{}])[0].get('message',{});answer=message.get('content','')
        try:value=json.loads(answer)
        except (ValueError,TypeError):value={}
        if not isinstance(value,dict):value={}
        result['needle_matches']={k:value.get(k)==v for k,v in meta['expected'].items()}
        result['all_five_needles_correct']=all(result['needle_matches'].values())
        result['tool_calls_requested']=len(message.get('tool_calls',[]))
        result['provider_usage_observed']={k:v for k,v in body.get('usage',{}).items() if isinstance(v,(int,float))}
    except urllib.error.HTTPError as error:
        result['http_status']=error.code
        try:
            error_value=json.loads(error.read(8192)).get('error',{})
            result['error_code']=error_value.get('code') if isinstance(error_value,dict) else 'unstructured_error'
        except ValueError:result['error_code']='non_json_error'
    except (urllib.error.URLError,TimeoutError):result['transport_error']='connection_or_timeout'
    result['elapsed_ms']=round((time.monotonic()-started)*1000)
    path=directory/'public-result.json';path.write_text(json.dumps(result,indent=2)+'\n');path.chmod(0o600);print(json.dumps(result),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);args=parser.parse_args();main(args.directory.resolve())
