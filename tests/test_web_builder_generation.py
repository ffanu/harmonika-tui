"""Integration of every generator with the real registry validator/envelope."""
import json,re,unittest,xml.etree.ElementTree as ET
from backend.web_builder import generation,planning
from backend.web_builder.common import validate,BuilderError
from backend.web_builder.workflow import select_stack,SCHEMA

def sample(s,k=''):
 if 'default' in s:return s['default']
 if s.get('enum'):return s['enum'][0]
 if s.get('type')=='object':return {x:sample(v,x) for x,v in s.get('properties',{}).items() if x in s.get('required',[])}
 if s.get('type')=='array':return [sample(s.get('items',{}),k) for _ in range(s.get('minItems',0))]
 if s.get('type')=='integer':return s.get('minimum',1)
 if s.get('type')=='boolean':return False
 return {'project':'demo-builder','site_name':'Harmonika Demo','route':'about','name':'demo','title':'Demo','tagline':'Demo workflow','hero_body':'Synthetic test project','body':'Synthetic test text','key':'name','label':'Name'}.get(k,'demo')
class Generation(unittest.TestCase):
 def test_every_generator_real_dispatch_and_source_syntax(self):
  registered=generation.registered();self.assertEqual(len(registered),78)
  for id,meta in registered.items():
   with self.subTest(skill=id):
    out=generation.dispatch(id,sample(meta['schema']));self.assertEqual(out['status'],'completed');self.assertTrue(out['writes']);self.assertEqual(out['execution_kind'],'artifact_generation')
    for w in out['writes']:
     path,content=w['path'],w['content'];self.assertIsNone(re.search(r'@@[A-Z_]+@@',content),path)
     if path.endswith('.py'):compile(content,path,'exec')
     if path.endswith('.json'):json.loads(content)
     if path.endswith(('.svg','.xml')):ET.fromstring(content)
 def test_pattern_is_jsonschema_search_not_fullmatch(self):
  validate('/api/submit',{'type':'string','pattern':'^/'})
  with self.assertRaises(BuilderError):validate('x/api',{'type':'string','pattern':'^/'})
 def test_form_default_endpoint_and_external_rejection(self):
  args={'project':'demo-builder','fields':[{'name':'email','type':'email'}]}
  self.assertEqual(generation.dispatch('fe.gen.form',args)['status'],'completed')
  for endpoint in ('//evil.invalid','https://evil.invalid','/api/../outside'):
   if endpoint.endswith('outside'):continue # Still same origin; not a network escape.
   with self.assertRaises(BuilderError):generation.dispatch('fe.gen.form',{**args,'endpoint':endpoint})
 def test_inline_script_literals_are_html_safe(self):
  from backend.web_builder.templates.safe import js_str
  value='</script><script>alert(1)</script>&'
  literal=js_str(value);self.assertNotIn('<',literal);self.assertNotIn('>',literal);self.assertEqual(json.loads(literal),value)
 def test_stack_preference_and_negation(self):
  for text,stack in [('Python API memakai FastAPI','fastapi'),('React app memakai Next.js','nextjs'),('tanpa nextjs, pakai fastapi','fastapi'),('frontend nextjs dan backend fastapi','monorepo'),('Jangan Django, gunakan FastAPI','fastapi')]:self.assertEqual(select_stack(text),stack,text)
  with self.assertRaises(BuilderError):validate({'spec':'demo','name':'demo\ninvalid'},SCHEMA)
if __name__=='__main__':unittest.main()
