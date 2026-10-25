"""Versioned pure artifact dispatch; no generator executes host code or network calls."""
import copy,importlib
from .common import BuilderError,require,validate,safe_path
MODULES=('scaffolds','frontend','backend_api','database','deploy','testing','ops')
def registered():
 result={}
 for name in MODULES:
  try:m=importlib.import_module('.templates.'+name,__package__)
  except ModuleNotFoundError as e:
   if e.name==__package__+'.templates.'+name:continue
   raise
  for id,meta in m.SKILLS.items():
   require(id not in result,'duplicate_generator_id',503);result[id]=meta
 return result
def schema(skill):
 meta=registered().get(skill);require(meta is not None,'generator_pending',503);return copy.deepcopy(meta['schema'])
def dispatch(skill,args):
 meta=registered().get(skill);require(meta is not None,'generator_pending',503);values=copy.deepcopy(args)
 for key,spec in meta['schema'].get('properties',{}).items():
  if key not in values and 'default' in spec:values[key]=copy.deepcopy(spec['default'])
 from .registry import schema_for
 validate(values,schema_for(skill))
 try:out=meta['handler'](values)
 except ValueError:raise BuilderError('invalid_generation_input') from None
 require(type(out) is dict and type(out.get('writes')) is list,'invalid_generator_output',502);writes=[];seen=set();total=0
 for item in out['writes']:
  if type(item) is dict:path=item['path'];content=item['content']
  else:path,content=item
  safe_path(path);require(path not in seen,'duplicate_generated_path',502);seen.add(path);require(type(content) is str and len(content.encode())<=524288,'generated_file_limit',413);total+=len(content.encode());require(total<=16*1024*1024,'generation_size_limit',413);writes.append({'path':path,'content':content})
 return {'status':'completed','execution_kind':'artifact_generation','data':out.get('data',{}),'writes':writes,'limitations':out.get('limitations',[])}
