import importlib.util,io,json,unittest,urllib.error
from pathlib import Path
from unittest.mock import Mock
s=importlib.util.spec_from_file_location('gateway',Path(__file__).resolve().parents[1]/'runtime/tools/tui_gateway.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
KEY='fixture-token'
def frame(content=None,reason=None,tool=None,finish=None):
 d={}
 if content is not None:d['content']=content
 if reason is not None:d['reasoning_content']=reason
 if tool:d['tool_calls']=tool
 return {'choices':[{'index':0,'delta':d,'finish_reason':finish}]}
def response(parts,done=True):return io.BytesIO((''.join('data: '+json.dumps(p)+'\n\n' for p in parts)+('data: [DONE]\n\n' if done else '')).encode())
class Gateway(unittest.TestCase):
 def engine(self,open_request):return m.Engine(KEY,{'fallback_enabled':True},open_request,lambda:{'ready':True,'model':'local-fixture','tools':True})
 def test_reasoning_split_and_code_literal(self):
  n=m.Normalizer();answer=[];reason=[]
  for chunk in ['<thi','nk>private','</th','ink>hello']:
   result,_=n.accept(frame(chunk))
   if result:answer.append(result['choices'][0]['delta'].get('content',''));reason.append(result['choices'][0]['delta'].get('reasoning_content',''))
  n.end();self.assertEqual(''.join(answer),'hello');self.assertEqual(''.join(reason),'private')
  n=m.Normalizer();value,_=n.accept(frame('```\n<think>literal</think>\n```'));self.assertIn('<think>',value['choices'][0]['delta']['content'])
 def test_unclosed_reasoning_refused(self):
  n=m.Normalizer();n.accept(frame('<think>hidden'))
  with self.assertRaises(m.GatewayError):n.end()
 def test_cloud_no_fallback_and_tool_passthrough(self):
  calls=[];tool=[{'index':0,'id':'a','type':'function','function':{'name':'read','arguments':'{}'}}]
  def open_request(req):calls.append(req);return response([frame(tool=tool),frame(finish='tool_calls')])
  events=list(self.engine(open_request).stream({'messages':[{'role':'user','content':'test'}],'stream':True}))
  self.assertEqual(len(calls),1);self.assertEqual(events[0]['choices'][0]['delta']['tool_calls'],tool);self.assertEqual(events[-1],'[DONE]')
 def test_failover_before_first_delta_key_not_forwarded(self):
  calls=[]
  def open_request(req):
   calls.append(req)
   if len(calls)==1:raise urllib.error.URLError('private failure')
   self.assertEqual(req.get_header('Authorization'),'Bearer local');self.assertEqual(json.loads(req.data)['model'],'local-fixture')
   return response([frame('offline'),frame(finish='stop')])
  self.assertEqual(list(self.engine(open_request).stream({'messages':[{'role':'user','content':'a'}],'stream':True}))[-1],'[DONE]');self.assertEqual(len(calls),2)
 def test_no_retry_auth_or_quota(self):
  for status in [401,403,429,400]:
   req=Mock(side_effect=urllib.error.HTTPError('url',status,'private',None,None))
   with self.assertRaises(m.GatewayError):list(self.engine(req).stream({'messages':[{'role':'user','content':'a'}],'stream':True}))
   self.assertEqual(req.call_count,1)
 def test_no_fallback_after_delta(self):
  class Interrupted(io.BytesIO):
   def readline(self,*a):
    data=super().readline(*a)
    if not data:raise TimeoutError('secret')
    return data
  req=Mock(return_value=Interrupted(response([frame('partial')],False).getvalue()))
  with self.assertRaises(m.GatewayError):list(self.engine(req).stream({'messages':[{'role':'user','content':'a'}],'stream':True}))
  self.assertEqual(req.call_count,1)
 def test_offline_missing_or_media_or_tools_refused(self):
  e=self.engine(Mock());e.ready=lambda:{'ready':False,'reason':'local_model_required'}
  with self.assertRaises(m.GatewayError):e.local_body({'messages':[]})
  e.ready=lambda:{'ready':True,'model':'a','tools':False}
  for body in [{'messages':[],'tools':[{}]},{'messages':[{'content':[{'type':'image_url'}]}]}]:
   with self.assertRaises(m.GatewayError):e.local_body(body)
 def test_local_discovery_rejects_cloud_model(self):
  def req(_):return io.BytesIO(json.dumps({'models':[{'name':'remote:cloud','size':100}]}).encode())
  self.assertFalse(m.local_ready({},req)['ready'])
 def test_nonstream_reasoning(self):
  value={'choices':[{'message':{'role':'assistant','content':'<think>private</think>answer'}}]}
  result=self.engine(lambda _:io.BytesIO(json.dumps(value).encode())).complete({'messages':[{'role':'user','content':'a'}]})
  self.assertEqual(result['choices'][0]['message']['content'],'answer');self.assertEqual(result['choices'][0]['message']['reasoning_content'],'private')
 def test_real_loopback_auth_health_and_stream(self):
  import threading,urllib.request
  server=m.Server(('127.0.0.1',0),m.Handler);server.engine=self.engine(lambda _:response([frame('<think>hidden</think>answer'),frame(finish='stop')]))
  server.engine.ready=lambda:{'ready':False,'reason':'local_runtime_unavailable'}
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   url='http://127.0.0.1:'+str(server.server_port)
   with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(url+'/health',timeout=2)
   self.assertEqual(err.exception.code,401)
   req=urllib.request.Request(url+'/health',headers={'Authorization':'Bearer '+KEY})
   with urllib.request.urlopen(req,timeout=2) as r:self.assertTrue(json.load(r)['ok'])
   req=urllib.request.Request(url+'/v1/chat/completions',data=json.dumps({'messages':[{'role':'user','content':'hello'}],'stream':True}).encode(),headers={'Authorization':'Bearer '+KEY})
   with urllib.request.urlopen(req,timeout=2) as r:body=r.read().decode()
   frames=[json.loads(part[6:]) for part in body.splitlines() if part.startswith('data: {')]
   answer=''.join(f['choices'][0]['delta'].get('content','') for f in frames)
   self.assertEqual(answer,'answer');self.assertIn('data: [DONE]',body)
  finally:server.shutdown();server.server_close();thread.join(2)
 def test_stream_error_is_terminal_without_done(self):
  e=self.engine(lambda _:response([frame('partial')],False));events=[]
  with self.assertRaises(m.GatewayError):
   for event in e.stream({'messages':[{'role':'user','content':'test'}],'stream':True}):events.append(event)
  self.assertNotIn('[DONE]',events)
 def test_empty_content_keeps_reasoning_tools_and_terminal(self):
  n=m.Normalizer();value,meaningful=n.accept(frame('',reason='structured'))
  self.assertTrue(meaningful);self.assertEqual(value['choices'][0]['delta']['reasoning_content'],'structured')
  tool=[{'id':'fixture'}];value,meaningful=n.accept(frame('',tool=tool));self.assertTrue(meaningful);self.assertEqual(value['choices'][0]['delta']['tool_calls'],tool)
  value,_=n.accept(frame('<',finish='stop'));self.assertEqual(value['choices'][0]['delta']['content'],'<')
 def test_nonstream_empty_tool_content(self):
  tool={'id':'a','type':'function','function':{'name':'read','arguments':'{}'}}
  value={'choices':[{'message':{'role':'assistant','content':'','tool_calls':[tool]}}]}
  result=self.engine(lambda _:io.BytesIO(json.dumps(value).encode())).complete({'messages':[{'role':'user','content':'a'}]})
  self.assertEqual(result['choices'][0]['message']['tool_calls'],[tool])
 def test_no_failover_after_empty_terminal_frame(self):
  class Interrupted(io.BytesIO):
   def read1(self,*a):
    chunk=super().read1(*a)
    if not chunk:raise TimeoutError('private')
    return chunk
  req=Mock(return_value=Interrupted(response([frame(finish='stop')],False).getvalue()))
  events=[]
  with self.assertRaises(m.GatewayError):
   for event in self.engine(req).stream({'messages':[{'role':'user','content':'a'}],'stream':True}):events.append(event)
  self.assertEqual(req.call_count,1);self.assertEqual(len(events),0)  # terminal is withheld until validated completion
