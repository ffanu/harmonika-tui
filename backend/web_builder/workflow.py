"""Spec-driven build orchestration. No deployment without a registered execution path."""
import asyncio,base64,json,re
from .common import require,canonical
SCHEMA={'type':'object','properties':{'spec':{'type':'string','minLength':1,'maxLength':50000},'stack':{'type':'string','enum':['auto','nextjs','fastapi','express','nuxt','sveltekit','remix','nestjs','django','laravel','monorepo']},'run_checks':{'type':'boolean'},'name':{'type':'string','pattern':'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$','minLength':2,'maxLength':50}},'required':['spec','name'],'additionalProperties':False}
def match_args(schema,values):
 out={}
 for k,s in schema.get('properties',{}).items():
  if k in values and ('enum' not in s or values[k] in s['enum']):out[k]=values[k]
  elif 'default' in s:out[k]=s['default']
  elif k in schema.get('required',[]):
   if s.get('enum'):out[k]=s['enum'][0]
   elif s.get('type')=='string':out[k]=values.get('name','project') if 'name' in k else values.get('spec','')
   elif s.get('type')=='array':out[k]=[]
   elif s.get('type')=='object':out[k]={}
   else:raise ValueError('workflow_schema_mapping_required')
 return out
def select_stack(spec):
 aliases={'nextjs':['nextjs','next.js'],'fastapi':['fastapi'],'django':['django'],'laravel':['laravel'],'nestjs':['nestjs'],'express':['express'],'nuxt':['nuxt'],'sveltekit':['sveltekit','svelte'],'remix':['remix'],'monorepo':['monorepo']}
 positive=set();negative=set()
 neg=re.compile(r'\b(?:tanpa|without|jangan|no|not|exclude|tidak\s+(?:perlu|pakai))\b')
 for clause in re.split(r'[,;.!?]|\b(?:tetapi|tapi|namun|but|instead)\b',spec.lower().replace('next.js','nextjs')):
  for stack,words in aliases.items():
   for word in words:
    for m in re.finditer(r'(?<![a-z])'+re.escape(word)+r'(?![a-z])',clause):
     (negative if neg.search(clause[:m.start()]) else positive).add(stack)
 choices=positive-negative
 # Explicit frontend+backend pairing bootstraps the monorepo, not a silent last-match overwrite.
 if len(choices)>1:return 'monorepo'
 if choices:return next(iter(choices))
 for word,stack in [('python','fastapi'),('php','laravel'),('vue','nuxt'),('react','nextjs')]:
  if re.search(r'\b'+word+r'\b',spec.lower()) and not re.search(r'\b(?:tanpa|without|jangan|no|not)\s+'+word+r'\b',spec.lower()):return stack
 return 'fastapi' if 'nextjs' in negative else 'nextjs'
async def build(rt,args,files,j,actor):
 from . import planning,generation
 from .operations import sandbox_run
 spec=args['spec'];name=args['name'];stack=args.get('stack','auto')
 if stack=='auto':stack=select_stack(spec)

 values={'text':spec,'spec':spec,'name':name,'project':name,'project_name':name,'framework':stack,'stack':stack,'description':spec}
 if stack=='nextjs':values['template']='harmonika-fullstack'
 requirements=await asyncio.to_thread(planning.dispatch,'spec.parse.requirements',match_args(planning.schema('spec.parse.requirements'),values))
 scaffold=await asyncio.to_thread(generation.dispatch,'scaffold.'+stack,match_args(generation.schema('scaffold.'+stack),values))
 require(scaffold.get('status')=='completed' and scaffold.get('writes'),'scaffold_failed',422)
 writes=list(scaffold['writes']);writes.append({'path':'docs/BUILD-SPEC.md','content':spec+'\n'});writes.append({'path':'docs/BUILD-REPORT.json','content':json.dumps({'stack':stack,'requirements':requirements.get('data'),'installed':False,'tests_executed':False,'deployed':False},ensure_ascii=False,indent=2)+'\n'})
 check=None
 if args.get('run_checks'):
  snapshot=dict(files)
  for item in writes:snapshot[item['path']]=base64.b64encode(item['content'].encode()).decode()
  # Syntax preflight is actually executed. Framework runtime tests are separate
  # explicit test jobs, never inferred from parsing or successful file generation.
  script="import pathlib,ast,json\ncount=0\nfor p in pathlib.Path('.').rglob('*.py'):\n ast.parse(p.read_text());count+=1\nfor p in pathlib.Path('.').rglob('*.json'):\n json.loads(p.read_text());count+=1\nassert count>0\nprint(json.dumps({'syntax_files_checked':count,'framework_tests_executed':False}))"
  import shlex
  check=await sandbox_run(rt,snapshot,j,'python3 -c '+shlex.quote(script),60)
 return {'status':'completed','execution_kind':'artifact_generation_and_syntax_check' if check else 'artifact_generation','data':{'stack':stack,'requirements':requirements.get('data'),'requirements_review_required':True, 'checks':check.get('data') if check else None,'framework_tests_executed':False,'deployed':False,'next_steps':['Install the pinned dependencies or use the preloaded sandbox toolchain.','Run the generated unit/integration/browser tests.','Review a registered deployment target before production.']},'writes':writes,'limitations':['Templates are not a promise that every free-text feature has been implemented. Review generated requirements and test the actual business workflow.']}
