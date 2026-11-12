"""Build a standalone, hash-pinned pure zimbra.qa module from reviewed knowledge."""
import hashlib
import json
from pathlib import Path

from .knowledge import QA


def render():
    identity={'id':'zimbra.qa','name':'Q&A pemulihan Zimbra','core_capabilities':['zimbra_recovery_qa'],
              'requirements':[],'input_schema':{'type':'object','properties':{
                  'topic':{'type':'string','enum':['index',*QA],'maxLength':48}},'additionalProperties':False}}
    context={'max_input_tokens':4096,'max_output_tokens':32768,'max_chat_messages':0,'max_tool_calls':0}
    source=Path(__file__).with_name('knowledge.py').read_text()
    source+='\n\ndef GetSkillIdentity():\n    return '+repr(identity)+'\n'
    source+='\n\ndef GetContextRequirements():\n    return '+repr(context)+'\n'
    source+='\n\nasync def Execute(payload):\n    return lookup(payload.get("topic","index"))\n'
    return source.encode('utf-8')


def build(directory):
    directory=Path(directory);directory.mkdir(mode=0o755,parents=True,exist_ok=False)
    source=render();(directory/'skill.py').write_bytes(source);(directory/'skill.py').chmod(0o644)
    (directory/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(source).hexdigest()})+'\n')
    (directory/'manifest.json').chmod(0o644)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory')
    build(parser.parse_args().directory)
