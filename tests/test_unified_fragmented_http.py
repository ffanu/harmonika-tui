import json,unittest
from backend.unified_engine.pipeline import read_bounded,Adapters
from backend.unified_engine.state import Error
class FragmentedResponse:
 def __init__(self,chunks):self.chunks=list(chunks);self.content=self;self.status=200
 async def read(self,n):return self.chunks.pop(0) if self.chunks else b''
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
class Client:
 def __init__(self,response):self.response=response
 def post(self,*args,**kwargs):return self.response
class Fragments(unittest.IsolatedAsyncioTestCase):
 async def test_collect_until_eof_not_first_available_chunk(self):
  response=FragmentedResponse([b'{"n":',b'9007199254740993',b'}']);raw=await read_bounded(response,100);self.assertEqual(json.loads(raw)['n'],9007199254740993)
 async def test_fragmented_model_response(self):
  raw=json.dumps({'choices':[{'message':{'content':'complete synthetic response'}}]}).encode();adapter=Adapters(Client(FragmentedResponse([raw[:7],raw[7:20],raw[20:]])));out=await adapter.chat([{'role':'user','content':'test'}],'Bearer hai_synthetic');self.assertEqual(out['text'],'complete synthetic response')
 async def test_limit_applies_to_total_not_each_chunk(self):
  with self.assertRaisesRegex(Error,'upstream_output_limit'):await read_bounded(FragmentedResponse([b'1234',b'5678']),7)
if __name__=='__main__':unittest.main()
