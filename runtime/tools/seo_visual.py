"""Broker headless Chromium network through the pinned-address HTTP fetcher."""
import base64
import ctypes
import errno
import tempfile
import json
import os
from pathlib import Path
import shutil
import subprocess

def browser_capabilities():
    node=os.environ.get('HARMONIKA_NODE') or shutil.which('node')
    if not node:return False
    try:
        result=subprocess.run([node,str(Path(__file__).with_suffix('.cjs')),'--capabilities'],capture_output=True,timeout=5)
        return result.returncode==0 and json.loads(result.stdout).get('available') is True
    except Exception:return False

def _capture_staged(payload,fetcher):
    node=os.environ.get('HARMONIKA_NODE') or shutil.which('node')
    if not node or not browser_capabilities():return {'status':'unavailable','reason':'browser_runtime_missing'}
    out=Path(payload['output_dir']);cache={}
    proc=subprocess.Popen([node,str(Path(__file__).with_suffix('.cjs'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        proc.stdin.write(json.dumps({'type':'init','url':payload['url'],'output_dir':str(out)})+'\n');proc.stdin.flush()
        for line in proc.stdout:
            if len(line)>32768:raise ValueError('browser_protocol_limit')
            message=json.loads(line)
            if message.get('type')=='viewport':
                from seo_network import SafeFetcher
                fetcher=SafeFetcher(max_requests=20,max_bytes=2097152,budget_seconds=22,timeout_seconds=8)
                cache={key:value for key,value in cache.items() if 'error' not in value}
                continue
            if message.get('type')=='result':
                result=message['result'];result['content_is_untrusted']=True
                proc.stdin.close();proc.wait(timeout=5)
                return result
            if message.get('type')=='fetch':
                url=message.get('url','');reply={'id':message.get('id')}
                try:
                    if not isinstance(url,str) or len(url)>2048:raise ValueError('invalid_resource_url')
                    if url not in cache:cache[url]=fetcher(url)
                    page=cache[url]
                    if 'error' in page:raise ValueError('cached_resource_failure')
                    if page['url']!=url:
                        reply.update(status=302,headers={'location':page['url']},body_base64='')
                        cache[page['url']]=page
                    else:
                        raw=page.get('body_bytes',page['text'].encode())
                        reply.update(status=page['status'],headers={'content-type':page.get('content_type','text/plain')},body_base64=base64.b64encode(raw).decode())
                except Exception as error:
                    reply['error']=getattr(error,'code','resource_blocked')
                    cache[url]={'error':reply['error']}
                proc.stdin.write(json.dumps(reply)+'\n');proc.stdin.flush()
        return {'status':'degraded','reason':'visual_capture_failed','output_dir':str(out),'checks':[]}
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()


def publish_directory(source,target):
    """Atomic directory publication without replacing even an existing empty directory."""
    libc=ctypes.CDLL(None,use_errno=True)
    if hasattr(libc,'renamex_np'):
        fn=libc.renamex_np;fn.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
        result=fn(os.fsencode(source),os.fsencode(target),4)  # Darwin RENAME_EXCL, sys/stdio.h.
    elif hasattr(libc,'renameat2'):
        fn=libc.renameat2;fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
        result=fn(-100,os.fsencode(source),-100,os.fsencode(target),1)  # Linux RENAME_NOREPLACE.
    else:raise OSError(errno.ENOTSUP,'Atomic exclusive directory publication unavailable')
    if result:
        code=ctypes.get_errno()
        if code in (errno.EEXIST,errno.ENOTEMPTY):raise FileExistsError(code,'Output directory exists')
        raise OSError(code,'Atomic exclusive directory publication failed')


def capture(payload,fetcher):
    out=Path(payload['output_dir'])
    if out.exists():raise FileExistsError('Output directory exists')
    if not out.parent.is_dir():return {'status':'unavailable','reason':'output_parent_missing'}
    # Browser failure cannot leave the final destination occupied.
    with tempfile.TemporaryDirectory(prefix='harmonika-seo-capture-',dir=str(out.parent)) as temporary:
        stage=Path(temporary)/'frames'
        result=_capture_staged(dict(payload,output_dir=str(stage)),fetcher)
        checks=result.get('checks',[])
        if len(checks)!=2 or any(not check.get('screenshot') or not Path(check['screenshot']).is_file() for check in checks):
            return {'schema_version':1,'status':'unavailable' if result.get('status')=='unavailable' else 'degraded','reason':result.get('reason','visual_capture_incomplete'),'content_is_untrusted':True,'checks':[{'name':check.get('name'),'error':check.get('error','capture_incomplete')} for check in checks],'artifacts':[]}
        for check in checks:check['screenshot']=str(out/Path(check['screenshot']).name)
        result['output_dir']=str(out);result['report_path']=str(out/'report.json')
        result['network_budget']={'requests_per_viewport':20,'seconds_per_viewport':22,'max_viewports':2}
        report=stage/'report.json'
        with report.open('x',encoding='utf-8') as handle:json.dump(result,handle,ensure_ascii=False,indent=2)
        report.chmod(0o600)
        publish_directory(stage,out)
        return result
