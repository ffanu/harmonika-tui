import io,json,unittest
from unittest.mock import patch
from backend.operations_engine import binding
class GraphicsBindingTests(unittest.TestCase):
 def test_graphics_reads_registry_discovery_not_capabilities(self):
  class Token:
   def read_text(self):return 'fixture-private-token-only'
  calls=[]
  class Client:
   def open(self,request,timeout):
    calls.append((request.full_url,timeout));return io.BytesIO(json.dumps({'diagnostics':{'graphics':{'status':'online','private_url':'DO_NOT_RETURN'}}}).encode())
  with patch.object(binding,'private_path',return_value=Token()),patch.object(binding.urllib.request,'build_opener',return_value=Client()):
   result=binding.graphics('api-token:1')
  self.assertEqual(calls,[('http://127.0.0.1:18193/skills/discovery',3)]);self.assertEqual(result,{'status':'online','source':'existing_registry_graphics_probe'})
 def test_invalid_graphics_payload_does_not_claim_online(self):
  with patch.object(binding,'private_path',side_effect=OSError):self.assertEqual(binding.graphics('api-token:1')['status'],'unavailable')
if __name__=='__main__':unittest.main()
