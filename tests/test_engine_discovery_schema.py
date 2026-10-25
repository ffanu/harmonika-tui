import unittest
from backend.engine_registry.schema import audit,validate,SchemaError

class SkillSchemas(unittest.TestCase):
 def test_strict_typed_fields(self):
  schema=audit({'type':'object','properties':{'text':{'type':'string','minLength':1,'maxLength':10},'count':{'type':'integer','minimum':1,'maximum':3}},'required':['text'],'additionalProperties':False})
  self.assertEqual(validate({'text':'Halo'},schema),{'text':'Halo'})
  for value in ({},{'text':''},{'text':'x'*11},{'text':'ok','extra':1},{'text':'ok','count':True},{'text':'ok','count':4},[]):
   with self.subTest(value=value),self.assertRaises(SchemaError):validate(value,schema)
 def test_invalid_schemas_fail_closed(self):
  for value in (None,{},True,{'type':'object','$ref':'https://bad.test/schema'},{'type':'object','required':['missing']},{'type':'object','additionalProperties':{}},{'type':'array'},{'type':'string','pattern':'(a+)+$'},{'type':'string','maxLength':True},{'type':'string','minLength':5,'maxLength':2},{'type':'string','minimum':1},{'type':'number','minimum':float('inf')},{'type':'object','required':'text'},{'type':'string','enum':[]},{'type':'string','enum':[{}]},{'type':'object','properties':[]},{'type':'object','properties':{'x':{'type':'unsupported'}}},{'type':'boolean','items':{'type':'string'}}):
   with self.subTest(value=value),self.assertRaises(SchemaError):audit(value)
 def test_nested_arrays_enums_numbers(self):
  schema=audit({'type':'array','minItems':1,'maxItems':2,'items':{'type':'object','properties':{'x':{'type':'number','minimum':0,'maximum':10},'kind':{'type':'string','enum':['a','b']}},'required':['x','kind']}})
  validate([{'x':1,'kind':'a'}],schema)
  for value in ([],[{'x':float('nan'),'kind':'a'}],[{'x':1,'kind':'bad'}],[{'x':-1,'kind':'a'}]):
   with self.subTest(value=value),self.assertRaises(SchemaError):validate(value,schema)
 def test_boolean_not_integer_enum(self):
  schema=audit({'type':'integer','enum':[1]})
  with self.assertRaises(SchemaError):validate(True,schema)
 def test_schema_complexity_bounded(self):
  schema={'type':'string'}
  for _ in range(10):schema={'type':'array','items':schema}
  with self.assertRaises(SchemaError):audit(schema)
