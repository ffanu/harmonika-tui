import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.web_builder.notion import read,settings
from backend.web_builder.common import BuilderError
PAGE='11111111-1111-4111-8111-111111111111'
class Notion(unittest.TestCase):
 def config(self):return SimpleNamespace(notion={'demo':{'principals':['api-token:1'],'page_ids':[PAGE]}})
 def test_scope_binding_before_network(self):
  for a,p in [('api-token:2',PAGE),('api-token:1','22222222-2222-4222-8222-222222222222')]:
   with self.assertRaises(BuilderError):settings(self.config(),a,p)
 def test_actual_source_text_and_pagination_truncation(self):
  data={'results':[{'type':'paragraph','paragraph':{'rich_text':[{'plain_text':'Synthetic spec: no payment.'}]},'has_children':False}],'has_more':True,'next_cursor':PAGE}
  with patch('backend.web_builder.notion.request',return_value=data) as req:v=read(self.config(),'api-token:1',PAGE)
  self.assertEqual(req.call_count,3);self.assertTrue(v['data']['truncated']);self.assertIn('Synthetic spec',v['data']['text']);self.assertNotIn('deployed',v['data'])
