"""Expose the analyzer's bounded field contract as standard JSON Schema."""
from . import analyzers

def field_schema(field):
 kind=field['type'];out={'type':{'str':'string','int':'integer','num':'number','bool':'boolean','list':'array','dict':'object'}[kind]}
 if kind=='str':out['maxLength']=field.get('max_len',120000)
 if kind in ('int','num'):
  if 'min' in field:out['minimum']=field['min']
  if 'max' in field:out['maximum']=field['max']
 if kind=='list':
  out['maxItems']=field.get('max_items',500)
  if field.get('items'):out['items']=field_schema(field['items'])
 if kind=='dict':out.update(object_schema(field.get('fields',{}),field.get('allow_unknown',False)))
 if field.get('default') is not None:out['default']=field['default']
 return out

def object_schema(fields,allow_unknown=False):
 return {'type':'object','properties':{k:field_schema(f) for k,f in fields.items()},'required':[k for k,f in fields.items() if f.get('required')],'additionalProperties':allow_unknown}

def descriptors():
 return {id:{'description':s['description'],'input_schema':object_schema(s['input_schema'])} for id,s in analyzers.SKILLS.items()}
