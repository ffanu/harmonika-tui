"""Owner-only real RTX smoke. Reports metadata, never credentials or image bytes."""
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('redirect_refused')
def main():
    key=Path('/etc/harmonika-ai/rtx-queue-client.token').read_text().strip()
    base='http://127.0.0.1:18186';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(path,payload=None,idem=None,auth=True):
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+key
        if idem:headers['Idempotency-Key']=idem
        return opener.open(urllib.request.Request(base+path,data=None if payload is None else json.dumps(payload).encode(),headers=headers),timeout=6)
    try:
        request('/health',auth=False);raise RuntimeError('missing_auth_guard')
    except urllib.error.HTTPError as e:assert e.code==401
    with request('/health') as r:before=json.load(r)
    assert before['worker']['ready'] and before['worker']['generate']
    payload={'kind':'generate','prompt':'A clean geometric test image, a solid blue circle centered on a plain white background, no text, flat illustration.','size':'512x512'}
    started=time.monotonic()
    with request('/v1/rtx/jobs',payload,'rtx_queue_smoke_v1') as r:accepted=json.load(r)['job']
    job=accepted
    while time.monotonic()-started<125:
        with request('/v1/rtx/jobs/'+job['job_id']) as r:job=json.load(r)['job']
        if job['status'] in {'completed','failed','unknown','cancelled'}:break
        time.sleep(.2)
    assert job['status']=='completed',job.get('error')
    artifact=job['result']['artifacts'][0]
    with request('/v1/rtx/files/'+artifact['file_id']) as r:data=r.read(16*1024*1024+1);mime=r.headers.get_content_type()
    assert mime=='image/png' and data.startswith(b'\x89PNG\r\n\x1a\n') and hashlib.sha256(data).hexdigest()==artifact['sha256']
    with request('/v1/rtx/jobs',payload,'rtx_queue_smoke_v1') as r:duplicate=json.load(r)['job']
    assert duplicate['job_id']==job['job_id']
    report={'ok':True,'service':'harmonika-rtx-queue.service','base_url':base,'worker_transport':'private_ssh_tunnel','generate_real':'completed','job_id':job['job_id'],'artifact':{k:artifact[k] for k in ['file_id','mime_type','size_bytes','width','height','sha256','status']},'seconds':round(time.monotonic()-started,2),'idempotency':'same_job_no_second_render','auth_without_token':401,'vision_real_ready':before['worker']['analyze'],'existing_chat_services_restarted':False}
    Path('/opt/harmonika-ai/rtx-queue/smoke-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
if __name__=='__main__':main()
