"""Bundle reviewed pure planner/generator into operator auto-discovery modules."""
import ast,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from erp_engine import planner,schema,generator,operations,domain_analysis

def common_source():
 constants={k:getattr(planner,k) for k in ['PROFILES','CATALOG','BY_ID','DEPS','LABELS','CORE_RUNTIME','AVAILABLE','ANALYSIS_AVAILABLE']}
 constants.update({k:getattr(schema,k) for k in ['MODELS','BACK']});constants.update({k:getattr(operations,k) for k in ['GENERATOR_OPS','ANALYSIS_OPS']});constants['DOMAIN_OPS']=domain_analysis.DOMAIN_OPS;constants.update({'MAX_ARTIFACT':generator.MAX_ARTIFACT,'TEMPLATES':generator.template_files(),'STARTER_README':generator.starter_readme()})
 out='import copy,hashlib,json,re,unicodedata,base64,io,zipfile\n'+''.join(k+'='+repr(v)+'\n' for k,v in constants.items())
 for module in ['planner','schema','generator','domain_analysis','operations']:
  source=(ROOT/'backend/erp_engine'/f'{module}.py').read_text();tree=ast.parse(source)
  for node in tree.body:
   if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and getattr(node,'name','') not in ('template_files','starter_readme'):
    out+=ast.get_source_segment(source,node)+'\n\n'
 out+='def template_files():\n return copy.deepcopy(TEMPLATES)\ndef starter_readme():\n return STARTER_README\n'
 return out

def bundle(destination):
 destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
 common=common_source();ids=sorted(planner.AVAILABLE|operations.GENERATOR_OPS|operations.ANALYSIS_OPS|{'plan','generate','catalog'})
 for operation in ids:
  key='erp_all.'+operation
  if operation=='catalog':properties={};required=[]
  elif operation in operations.ANALYSIS_OPS:
   properties={'data':{'type':'object','additionalProperties':True}};required=['data']
  else:
   properties={'spec':{'type':'string','minLength':1,'maxLength':16000},'company':{'type':'string','minLength':1,'maxLength':100},'industry':{'type':'string','enum':list(planner.BY_ID)},'optional':{'type':'array','items':{'type':'string'},'maxItems':40},'exclude':{'type':'array','items':{'type':'string'},'maxItems':40},'branding':{'type':'object','additionalProperties':True},'components':{'type':'object','additionalProperties':True}};required=['spec']
  identity={'id':key,'name':'ERP '+operation,'core_capabilities':['source_backed_erp_planning' if operation not in operations.ANALYSIS_OPS else 'supplied_data_analysis_no_live_posting'],'requirements':[],'input_schema':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}
  call='invoke("catalog",payload)' if operation=='catalog' else 'invoke('+repr(operation)+',payload.get("data"))' if operation in operations.ANALYSIS_OPS else 'invoke('+repr(operation)+',payload)'
  source=common+'\ndef GetSkillIdentity():\n return '+repr(identity)+'\ndef GetContextRequirements():\n return {"max_input_tokens":65536,"max_output_tokens":262144,"max_chat_messages":0,"max_tool_calls":0}\nasync def Execute(payload):\n result='+call+'\n if "files" in result:\n  files=result.pop("files");result["source_file_count"]=len(files)\n return result\n'
  target=destination/key;target.mkdir(exist_ok=True);(target/'skill.py').write_text(source);(target/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}))
 return {'plugins':len(ids),'source_bytes_per_plugin':len(source.encode()),'ids':['erp_all.'+x for x in ids]}
if __name__=='__main__':print(json.dumps(bundle(sys.argv[1] if len(sys.argv)>1 else ROOT/'dist/erp-plugins')))
