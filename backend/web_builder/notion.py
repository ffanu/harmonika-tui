"""Tenant-authorized Notion page reader. Fixed API origin, bounded blocks, no arbitrary URLs.
Contract: https://developers.notion.com/reference/get-block-children
"""
import json,time,urllib.request,urllib.error
from uuid import UUID
from urllib.parse import urlencode
from .common import require,BuilderError
from .deployment import credential
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise BuilderError('notion_redirect_refused',502)
def settings(connectors,actor,page_id):
 try:page=str(UUID(page_id))
 except (ValueError,TypeError,AttributeError):raise BuilderError('invalid_notion_page') from None
 for t in getattr(connectors,'notion',{}).values():
  if actor in t.get('principals',[]) and page.replace('-','') in [str(x).replace('-','') for x in t.get('page_ids',[])]:return t,page
 raise BuilderError('notion_page_not_authorized',403)
def request(t,block,cursor=None):
 try:block=str(UUID(block))
 except (ValueError,TypeError,AttributeError):raise BuilderError('invalid_notion_response',502) from None
 query={'page_size':100}
 if cursor:require(type(cursor) is str and len(cursor)<=1000,'invalid_notion_response',502);query['start_cursor']=cursor
 req=urllib.request.Request('https://api.notion.com/v1/blocks/'+block+'/children?'+urlencode(query),headers={'Authorization':'Bearer '+credential(t),'Notion-Version':'2022-06-28','Accept':'application/json'})
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=10) as res:raw=res.read(1048577)
 except urllib.error.HTTPError:raise BuilderError('notion_provider_rejected',502) from None
 except (OSError,TimeoutError):raise BuilderError('notion_unavailable',503) from None
 require(len(raw)<=1048576,'notion_response_limit',502)
 try:v=json.loads(raw)
 except ValueError:raise BuilderError('invalid_notion_response',502) from None
 require(type(v) is dict and type(v.get('results')) is list,'invalid_notion_response',502);return v
def read(connectors,actor,page_id):
 t,page=settings(connectors,actor,page_id);pending=[(page,None)];lines=[];requests=0;deadline=time.monotonic()+30;truncated=False
 while pending and requests<3 and time.monotonic()<deadline:
  block,cursor=pending.pop(0);v=request(t,block,cursor);requests+=1
  for item in v['results'][:100]:
   kind=item.get('type');content=item.get(kind,{}) if type(kind) is str else {};parts=content.get('rich_text',[]) if type(content) is dict else [];text=''.join(x.get('plain_text','') for x in parts if type(x) is dict and type(x.get('plain_text','')) is str)
   if text:lines.append((('# ' if kind.startswith('heading') else '- ' if kind.endswith('list_item') else '')+text)[:10000])
   if item.get('has_children'):pending.append((item.get('id'),None))
  if v.get('has_more'):pending.insert(0,(block,v.get('next_cursor')))
  if sum(len(x) for x in lines)>50000:truncated=True;break
 return {'status':'completed','execution_kind':'registered_notion_read','data':{'page_id':page,'text':'\n'.join(lines)[:50000],'truncated':truncated or bool(pending),'requests':requests},'limitations':['Read only the pages explicitly authorized to this token. At most three paginated block requests; nested or long pages may be partial.']}
