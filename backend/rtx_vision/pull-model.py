"""One-time local model installation; no inference or cloud service credentials."""
import json,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).parent;MODEL='qwen3-vl:2b-instruct'
expected=json.loads((ROOT/'model-install-spec.json').read_text())['digest'];start=time.monotonic()
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
request=urllib.request.Request('http://127.0.0.1:11439/api/pull',data=json.dumps({'model':MODEL,'stream':True}).encode(),headers={'Content-Type':'application/json'})
last_write=0
with op.open(request,timeout=90) as r:
    for raw in r:
        if len(raw)>65536 or time.monotonic()-start>3600:raise SystemExit('model_pull_timeout')
        value=json.loads(raw)
        if value.get('error'):raise SystemExit('model_pull_failed')
        if time.monotonic()-last_write>1 or value.get('status')=='success':
            progress={k:value[k] for k in ('status','digest','total','completed') if k in value};progress['seconds']=round(time.monotonic()-start,1)
            (ROOT/'model-pull-status.json').write_text(json.dumps(progress));last_write=time.monotonic()
with op.open('http://127.0.0.1:11439/api/tags',timeout=5) as r:tags=json.load(r)
model=next((x for x in tags['models'] if x.get('name')==MODEL),None)
if model is None or model.get('digest')!=expected:raise SystemExit('model_digest_mismatch')
report={'status':'verified','digest':expected,'size_bytes':model['size'],'seconds':round(time.monotonic()-start,2),'cloud_inference_used':False}
(ROOT/'model-install-receipt.json').write_text(json.dumps(report));print(json.dumps(report))
