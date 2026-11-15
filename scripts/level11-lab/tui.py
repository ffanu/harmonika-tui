"""HARMONIKA | Atlas transport reference; not the native binary."""
import argparse,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
class SSEParser:
 def __init__(self,scope):self.scope=scope;self.cursor=None;self.buffer=b''
 def feed(self,chunk):
  self.buffer+=chunk
  # Deliberately broken: parses incomplete chunks and ignores scope/UTF-8/caps.
  return [json.loads(x[6:]) for x in chunk.decode().splitlines() if x.startswith('data: ')]
 def finish(self):return []
def main():
 from ops import call
 p=argparse.ArgumentParser();p.add_argument('--owner',choices=['alpha','beta'],required=True);p.add_argument('--project',choices=['one','two'],required=True);p.add_argument('--job',required=True);a=p.parse_args()
 result=call({'op':'http','owner':a.owner,'project':a.project,'method':'GET','path':'/jobs/'+a.job});assert result['http_status']==200
 import base64
 job=json.loads(base64.b64decode(result['body_b64']))['job'];events=call({'op':'http','owner':a.owner,'project':a.project,'method':'GET','path':'/events'});assert events['http_status']==200
 parser=SSEParser(hashlib.sha256((a.owner+'\0'+a.project).encode()).hexdigest()[:16]);raw=base64.b64decode(events['body_b64']);seen=[]
 for i in range(0,len(raw),7):seen+=parser.feed(raw[i:i+7])
 seen+=parser.finish();print(json.dumps({'brand':'HARMONIKA','job':job,'cursor':parser.cursor,'events':len(seen),'finite_page':True}))
if __name__=='__main__':main()
