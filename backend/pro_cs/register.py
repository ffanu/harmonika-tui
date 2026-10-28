"""Publish stateless analysis functions into the existing sandboxed skill registry."""
import json,hashlib,os
from pathlib import Path
import catalog
ROOT=Path('/opt/harmonika-ai/skills-runtime')
def publish():
 core=(Path(__file__).parent/'core.py').read_text();rows=catalog.entries()
 operations={'catalog':None,'analyze':'plan','validate':'validate','intent':'detect','privacy':'redact','extract':'facts'}
 for suffix,fn in operations.items():
  sid='pro_cs.'+suffix
  source='''def GetSkillIdentity():
 return IDENTITY
def GetContextRequirements():
 return {'max_input_tokens':65536,'max_output_tokens':131072,'max_chat_messages':0,'max_tool_calls':0}
async def Execute(payload):
 OPERATION
'''
  identity={'id':sid,'name':'Harmonika Pro CS '+suffix,'core_capabilities':['stateless_preview_analysis_no_sending'],'requirements':[],'input_schema':({'type':'object','properties':{'offset':{'type':'integer','minimum':0,'maximum':243},'limit':{'type':'integer','minimum':1,'maximum':100}},'additionalProperties':False} if suffix=='catalog' else {'type':'object','additionalProperties':True})}
  source=source.replace('IDENTITY',repr(identity))
  if fn:
   call="ns['"+fn+"'](payload)" if fn not in ['redact','detect'] else ("ns['detect'](payload.get('text'))" if fn=='detect' else "{'text':ns['redact'](payload.get('text'))}")
   operation='ns={};exec(compile('+repr(core)+',"<harmonika-pro-cs>","exec"),ns);return {"status":"completed","analysis_performed":True,"data":'+call+'}'
  else:operation='data='+repr({'catalog_count':243,'skills':rows,'auto_send':False,'live_channel_integration':False,'full_catalog_is_not_full_implementation':True})+';offset=payload.get("offset",0);limit=payload.get("limit",25);data["offset"]=offset;data["skills"]=data["skills"][offset:offset+limit];return {"status":"completed","analysis_performed":True,"data":data}'
  source=source.replace('OPERATION',operation);compile(source,sid,'exec');folder=ROOT/sid;folder.mkdir(exist_ok=True,mode=0o700)
  for name,b in [('skill.py',source.encode()),('manifest.json',json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}).encode())]:
   p=folder/(name+'.next');p.write_bytes(b);p.chmod(0o600);os.replace(p,folder/name)
if __name__=='__main__':publish()
