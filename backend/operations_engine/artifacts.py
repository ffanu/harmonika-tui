"""Owned browser artifact chunks. Content is not included in mechanics logs."""
import base64,json,re,time,hashlib
from pathlib import Path
from .common import fields,require,private_path
ID=re.compile(r'^browser_[a-f0-9]{32}/(?:trace.zip|screenshot.png)$')
def read(config,payload):
 fields(payload,{'artifact_id','offset','limit'},('artifact_id',));identifier=payload['artifact_id']
 require(type(identifier) is str and ID.fullmatch(identifier),'invalid_artifact_id')
 offset=payload.get('offset',0);limit=payload.get('limit',65536)
 require(type(offset) is int and offset>=0 and type(limit) is int and 1<=limit<=65536,'invalid_artifact_range')
 root=private_path(config['artifacts_root'],directory=True);target=private_path(root/identifier)
 retention=private_path(target.parent/'retention.json');require(retention.stat().st_size<=1024,'invalid_artifact_retention')
 meta=json.loads(retention.read_text());require(type(meta.get('expires_at')) is int and meta['expires_at']>time.time(),'artifact_expired',410)
 size=target.stat().st_size;require(size<=16*1024**2 and offset<=size,'invalid_artifact_range')
 with target.open('rb') as f:f.seek(offset);data=f.read(limit)
 whole_hash=hashlib.sha256(target.read_bytes()).hexdigest() if offset==0 else None
 return {'sha256':whole_hash,'status':'completed','artifact_id':identifier,'offset':offset,'next_offset':offset+len(data),'bytes':size,'complete':offset+len(data)==size,'chunk_sha256':hashlib.sha256(data).hexdigest(),'data_base64':base64.b64encode(data).decode(),'expires_at':meta['expires_at']}
