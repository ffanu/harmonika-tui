"""Read-only scheduled refresh of configured official public law documents."""
import json,urllib.request,urllib.error
from pathlib import Path
from .sources import MANIFEST

def main():
 secret=Path('/etc/harmonika-ai/legal-engine.token').read_text().strip();out=[]
 for id in MANIFEST:
  r=urllib.request.Request('http://127.0.0.1:18190/sources/refresh',data=json.dumps({'source_id':id}).encode(),method='POST',headers={'Content-Type':'application/json','X-Harmonika-Legal-Secret':secret,'X-Harmonika-Principal':'api-token:900000000000400000'})
  try:
   with urllib.request.urlopen(r,timeout=45) as response:v=json.load(response);out.append({'source_id':id,'status':'refreshed','sha256':v['source']['sha256'],'currentness_certified':False})
  except Exception:out.append({'source_id':id,'status':'unavailable','currentness_certified':False})
 r=urllib.request.Request('http://127.0.0.1:18190/_internal/poll-watches',data=b'{}',method='POST',headers={'Content-Type':'application/json','X-Harmonika-Legal-Secret':secret,'X-Harmonika-Principal':'api-token:900000000000400000'})
 try:
  with urllib.request.urlopen(r,timeout=90) as response:poll=json.load(response)
 except Exception:poll={'status':'unavailable'}
 print(json.dumps({'official_source_refresh':out,'watch_poll':poll,'case_or_evidence_data_sent':False}))
if __name__=='__main__':main()
