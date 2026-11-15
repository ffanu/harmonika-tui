"""RTX image delivery with a bounded, evidence-based bird anatomy gate."""
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from image_anatomy import contract, guide, review
from visual_media import ensure_media_port, port_open, png_result


class Pending(Exception):pass
class Rejected(Exception):pass


class Gateway:
    def __init__(self,port=18200,token_file=None):
        if port not in [18200,18203]:raise Rejected('invalid_gateway_port')
        if not (ensure_media_port(18200) if port==18200 else port_open(port)):raise Rejected('image_gateway_offline')
        self.port=port
        self.token=os.environ.get('HARMONIKA_RTX_GATEWAY_TOKEN','').strip()
        if not self.token:self.token=Path(token_file or Path.home()/'.config/harmonika-terminal/rtx-gateway.token').read_text().strip()
        if not self.token:raise Rejected('image_gateway_key_not_configured')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.jobs=[]

    def request(self,path,body=None,limit=262144,timeout=15,idem=None):
        req=urllib.request.Request('http://127.0.0.1:'+str(self.port)+path,data=None if body is None else json.dumps(body).encode(),headers={
            'Authorization':'Bearer '+self.token,'Content-Type':'application/json',**({'Idempotency-Key':idem} if idem else {})})
        with self.opener.open(req,timeout=timeout) as response:data=response.read(limit+1)
        if len(data)>limit:raise Rejected('image_result_too_large')
        return data

    def render(self,prompt,width,height,deadline):
        if time.monotonic()>=deadline:raise Rejected('image_quality_deadline_exceeded')
        try:
            value=json.loads(self.request('/v1/jobs',{'skill_id':'image.generate','input':{'prompt':prompt,'width':width,'height':height,'engine':'fluxhd','steps':28}},idem='image-qa-'+uuid.uuid4().hex))
        except urllib.error.HTTPError as error:
            if error.code>=500:raise Pending('image_submission_uncertain') from None
            raise Rejected('image_gateway_rejected') from None
        except (OSError,TimeoutError):raise Pending('image_submission_uncertain') from None
        jid=value.get('job_id','')
        if not re.fullmatch(r'gw_[a-f0-9]{16}',jid):raise Pending('image_submission_uncertain')
        self.jobs.append(jid)
        while time.monotonic()<deadline:
            time.sleep(min(3,max(0,deadline-time.monotonic())))
            try:job=json.loads(self.request('/v1/jobs/'+jid))
            except Exception:raise Pending('image_job_pending') from None
            if job.get('state')=='failed':raise Rejected('image_generation_failed')
            if job.get('state')!='completed':continue
            result=job.get('result',{});name=result.get('file','')
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}',name) or '..' in name:raise Rejected('invalid_artifact_path')
            raw=self.request('/files/'+name,limit=16*1024*1024,timeout=min(120,max(1,deadline-time.monotonic())))
            return raw,result,jid
        raise Pending('image_job_pending')


def generate(args,output_dir,vision_token,*,client=None,inspector=None):
    jobs=[];checks=[]
    try:
        prompt=args.get('prompt');width=args.get('width',2048);height=args.get('height',2048)
        if not isinstance(prompt,str) or not 1<=len(prompt)<=8000:return {'ok':False,'error':'invalid_prompt'}
        if any(type(v) is not int or not 256<=v<=2048 or v%8 for v in [width,height]):return {'ok':False,'error':'invalid_dimensions'}
        client=client or Gateway();spec=contract(prompt);deadline=time.monotonic()+570
        with tempfile.TemporaryDirectory(prefix='harmonika-image-review-') as temporary:
            for attempt in range(1,3 if spec else 2):
                if time.monotonic()>=deadline:raise Rejected('image_quality_deadline_exceeded')
                raw,result,jid=client.render(guide(prompt,spec),width,height,deadline);jobs.append(jid)
                metadata=png_result(raw,result,jid)
                if len(raw)<2*1024*1024:return {'ok':False,'error':'image_quality_not_met','job_ids':jobs}
                candidate=Path(temporary)/('candidate-'+str(attempt)+'.png');candidate.write_bytes(raw);candidate.chmod(0o600)
                gate=review(candidate,prompt,vision_token,inspector=inspector) if spec else {'status':'not_applicable'}
                checks.append(gate)
                if gate['status']=='failed':continue  # Only a completed, proven-bad render may get one correction.
                if gate['status']=='needs_review':return {'ok':False,'error':'image_visual_review_unavailable','job_ids':jobs,'quality_gate':gate,'uncertain':True}
                target=Path(output_dir).expanduser();target.mkdir(parents=True,exist_ok=True)
                file=target/('harmonika-high-quality-'+uuid.uuid4().hex[:12]+'.png')
                with file.open('xb') as out:
                    os.chmod(file,0o600);out.write(raw)
                if gate['status']=='passed':metadata['quality_verification']='bird_count_and_visible_anatomy_checked'
                return {'ok':True,'file':str(file),'bytes':len(raw),'quality':'high',**metadata,
                    'job_ids':jobs,'generation_attempts':attempt,'quality_gate':gate}
        return {'ok':False,'error':'image_visual_quality_failed','job_ids':jobs,'generation_attempts':len(jobs),'quality_gate':checks[-1]}
    except Pending as error:
        return {'ok':False,'error':str(error),'job_ids':getattr(client,'jobs',jobs),'uncertain':True}
    except Rejected as error:return {'ok':False,'error':str(error),'job_ids':jobs}
    except Exception:return {'ok':False,'error':'image_result_unavailable','job_ids':getattr(client,'jobs',jobs),'uncertain':True}


if __name__=='__main__':
    import sys
    try:
        body=json.loads(sys.stdin.read(65536));token=Path('/etc/harmonika-ai/rtx-vision-worker.token').read_text().strip()
        client=Gateway(port=18203,token_file='/etc/harmonika-ai/image-quality-gateway.token')
        result=generate(body['input'],body['output_dir'],token,client=client)
    except Exception:result={'ok':False,'error':'image_quality_transport_unavailable'}
    print(json.dumps(result))
