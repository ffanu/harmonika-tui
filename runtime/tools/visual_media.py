"""Local visual transport, accurate provenance and bounded private payloads."""
from pathlib import Path
import hashlib, io, json, os, re, socket, subprocess, time, urllib.error, urllib.request, uuid


def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), .5):return True
    except OSError:return False


def ensure_media_port(port):
    if port not in (18188,18200):return False
    if port_open(port):return True
    subprocess.run(['launchctl','kickstart',f'gui/{os.getuid()}/id.harmonika.media-tunnels'],
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
    end=time.monotonic()+10
    while time.monotonic()<end:
        if port_open(port):return True
        time.sleep(.25)
    return False


def png_result(raw, result, job_id):
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as im:
        if im.format!='PNG':raise ValueError('not_png')
        if not 256<=im.width<=2048 or not 256<=im.height<=2048:raise ValueError('image_dimensions_exceeded')
        im.verify()
    with Image.open(io.BytesIO(raw)) as im:
        im.load();width,height=im.size
    engine=result.get('engine')
    names={'flux2-klein-4b':'FLUX.2 Klein 4B','flux1-dev':'FLUX.1-dev','qwen-image-2512':'Qwen Image 2512'}
    return {'model':names.get(engine,'unreported'), 'engine':engine or 'unreported',
            'job_id':job_id,'width':width,'height':height,'sha256':hashlib.sha256(raw).hexdigest(),
            'quality_verification':'visual_review_required',
            'size_note':'PNG byte size alone does not prove visual quality'}


def request(path, token, body=None, content_type='application/json', job_id=None):
    req=urllib.request.Request('http://127.0.0.1:18188'+path,data=body,
        headers={'Authorization':'Bearer '+token,'Content-Type':content_type, **({'Idempotency-Key':job_id} if job_id else {})})
    # Fixed loopback only, bypass ambient proxies; never follow server redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(req,timeout=15) as r:
        data=r.read(262145)
    if len(data)>262144:raise ValueError('oversized_response')
    return json.loads(data)


def analyze(args, token):
    jid='job_mcp'+uuid.uuid4().hex
    submitted=False
    try:
        p=Path(args.get('path','')).expanduser()
        if not p.is_file():return {'ok':False,'error':'image_file_missing'}
        if p.stat().st_size>10*1024*1024:return {'ok':False,'error':'image_size_exceeded'}
        with p.open('rb') as source:raw=source.read(10*1024*1024+1)
        if len(raw)>10*1024*1024:return {'ok':False,'error':'image_size_exceeded'}
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('PNG','JPEG','WEBP'):return {'ok':False,'error':'unsupported_image'}
            if im.width>4096 or im.height>4096:return {'ok':False,'error':'image_dimensions_exceeded'}
            mime=Image.MIME[im.format];im.verify()
        prompt=args.get('prompt') or 'Jelaskan komposisi, objek, warna dan teks yang terlihat. Nyatakan ketidakpastian.'
        if not isinstance(prompt,str) or not 1<=len(prompt.encode())<=12000:return {'ok':False,'error':'invalid_prompt'}
        if not ensure_media_port(18188):return {'ok':False,'error':'vision_transport_unavailable'}
        meta={'schema_version':1,'kind':'analyze','job_id':jid,
              'request_digest':hashlib.sha256(raw+prompt.encode()).hexdigest(),'prompt':prompt,
              'reference_files':[{'field':'reference_0','mime_type':mime,'size_bytes':len(raw),
                                  'sha256':hashlib.sha256(raw).hexdigest()}]}
        boundary='---hai'+uuid.uuid4().hex
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="job"\r\nContent-Type: application/json\r\n\r\n'.encode()+json.dumps(meta).encode()+
              f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="reference_0"\r\nContent-Type: {mime}\r\n\r\n'.encode()+raw+f'\r\n--{boundary}--\r\n'.encode())
        submitted=True
        request('/v1/rtx/jobs',token,body,'multipart/form-data; boundary='+boundary, jid)
        for _ in range(75):
            time.sleep(2)
            value=request('/v1/rtx/jobs/'+jid,token)
            if value.get('status')=='completed':return {'ok':True,'job_id':jid,**value['result']}
            if value.get('status') in ('failed','unknown'):
                return {'ok':False,'job_id':jid,'error':value.get('error',{}).get('code','vision_failed')}
        return {'ok':False,'error':'vision_job_pending','job_id':jid,'uncertain':True}
    except urllib.error.HTTPError as e:
        return {'ok':False,'error':'vision_gateway_rejected','http_status':e.code,'job_id':jid}
    except Exception:
        return {'ok':False,'error':'vision_result_pending' if submitted else 'vision_input_unavailable',
                'job_id':jid,'uncertain':submitted}
