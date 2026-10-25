"""Real software Chromium on caller-provided HTML in a networkless container."""
import asyncio,base64,json,re,uuid
from pathlib import Path
SCHEMA={'type':'object','properties':{'html':{'type':'string','minLength':1,'maxLength':60000},'actions':{'type':'array','maxItems':12,'items':{'type':'object','properties':{'type':{'type':'string','enum':['dom.query','dom.text','dom.click','dom.fill','dom.check','dom.select','dom.wait_visible','dom.scroll','keyboard.press']},'selector':{'type':'string','maxLength':200},'value':{'type':'string','maxLength':1000}},'required':['type'],'additionalProperties':False}},'viewport':{'type':'object','properties':{'width':{'type':'integer','minimum':320,'maximum':1920},'height':{'type':'integer','minimum':240,'maximum':1080}},'required':['width','height'],'additionalProperties':False}},'required':['html','actions'],'additionalProperties':False}
DESCRIPTOR={'description':'Execute a bounded DOM interaction workflow on supplied HTML using isolated software Chromium; no remote site/profile access','input_schema':SCHEMA}
async def execute(payload,sandbox):
 from web_builder.common import validate
 validate(payload,SCHEMA)
 for action in payload['actions']:
  if action['type']!='keyboard.press' and not action.get('selector'):raise ValueError('selector_required')
  if action['type'] in ('dom.fill','dom.select','keyboard.press') and not action.get('value'):raise ValueError('value_required')
 files={'runner.cjs':base64.b64encode(Path(__file__).with_name('browser_worker.cjs').read_bytes()).decode(),'request.json':base64.b64encode(json.dumps(payload).encode()).decode()}
 result=await sandbox.run(files,'exec.shell',{'command':'node runner.cjs','timeout_seconds':20},str(uuid.uuid4()),deadline_seconds=35)
 if result.get('status')!='completed' or result.get('exit_code')!=0:raise ValueError('browser_workflow_failed')
 encoded=result.get('snapshot',{}).get('result.json')
 if not encoded:raise ValueError('browser_result_missing')
 data=json.loads(base64.b64decode(encoded));return {'status':'completed','analysis_performed':True,'execution_kind':'isolated_browser','data':data,'limitations':['Only supplied HTML was tested. No remote website, authenticated browser session, stealth patch, credential store, or host device was accessed. Screenshot hash proves capture; image bytes are not published.','Page script behavior remains untrusted; network/GPU/host access disabled.']}
