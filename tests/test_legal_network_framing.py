"""Real local socket fixtures: public fetcher must reject partial source bytes."""
import http.client,socket,threading,unittest
from backend.legal_engine.official_network import SafeFetcher,NetworkError
class FramingTests(unittest.TestCase):
 def fetch(self,headers,body):
  server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);port=server.getsockname()[1]
  def reply():
   try:
    client,_=server.accept()
    with client:client.recv(65536);client.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\n'+headers+b'\r\n'+body)
   finally:server.close()
  thread=threading.Thread(target=reply,daemon=True);thread.start()
  class Local(SafeFetcher):
   def _resolve_host(self,host):return ['1.2.3.4']
   def _open_connection(self,address,unused,hostname,https,timeout):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout);conn.connect();return conn
  try:return Local(max_bytes=1000,budget_seconds=2).fetch('https://peraturan.go.id/listing')
  finally:thread.join(timeout=3)
 def test_declared_length_truncation_rejected(self):
  with self.assertRaises(NetworkError) as cm:self.fetch(b'Content-Length: 400\r\n',b'X'*100)
  self.assertEqual(cm.exception.code,'response_incomplete')
 def test_complete_length_and_close_delimited_remain_valid(self):
  for headers in (b'Content-Length: 100\r\n',b''):
   self.assertEqual(len(self.fetch(headers,b'X'*100)['body_bytes']),100)
 def test_incomplete_chunked_rejected(self):
  with self.assertRaises(NetworkError):self.fetch(b'Transfer-Encoding: chunked\r\n',b'64\r\n'+b'X'*100+b'\r\n')
