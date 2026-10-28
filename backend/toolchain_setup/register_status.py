"""Publish sanitized readiness evidence using existing live-discovery contract."""
import hashlib,json,os
from pathlib import Path
ROOT=Path('/opt/harmonika-ai/skills-runtime/backend.tools.status')
def publish(report):
 data={k:report[k] for k in ['schema_version','checked_at','ok','checks','versions','scope','model_readiness']};data['installed_package_count']=len(Path('/opt/harmonika-ai/toolchain-runtime/requirements.lock').read_text().splitlines());data['is_readiness_snapshot']=True;data['installation_is_not_skill_activation']=True
 source="SNAPSHOT = "+repr(data)+'''\ndef GetSkillIdentity():
 return {'id':'backend.tools.status','name':'Harmonika backend toolchain readiness','core_capabilities':['verified_readiness_snapshot'],'requirements':[],'input_schema':{'type':'object','properties':{},'required':[],'additionalProperties':False}}
def GetContextRequirements():
 return {'max_input_tokens':1024,'max_output_tokens':16384,'max_chat_messages':0,'max_tool_calls':0}
async def Execute(payload):
 return {'status':'completed','analysis_performed':True,'data':SNAPSHOT}
'''
 ROOT.mkdir(exist_ok=True,mode=0o700)
 for name,raw in [('skill.py',source.encode()),('manifest.json',json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}).encode())]:
  temp=ROOT/(name+'.next');temp.write_bytes(raw);temp.chmod(0o600);os.replace(temp,ROOT/name)
if __name__=='__main__':publish(json.loads(Path('/opt/harmonika-ai/toolchain-runtime/verification.json').read_text()))
