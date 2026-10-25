"""Async HTTP adapter for a registered Windows RTX worker, with no blind retries."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import aiohttp
from .core import RTXError,RESULT_LIMIT,safe_id,validate_endpoint

class RTXAdapter:
    def __init__(self,endpoint,mode='v1',token='',connect_timeout=3,job_timeout=120,poll_interval=1):
        if mode not in {'v1','legacy'}:raise RTXError('invalid_worker_mode')
        self.endpoint=validate_endpoint(endpoint,mode);self.mode=mode;self.token=token
        if mode=='v1' and not token:raise RTXError('worker_secret_required')
        if not 0<connect_timeout<=30 or not 0<job_timeout<=600:raise RTXError('invalid_timeout')
        self.connect_timeout=connect_timeout;self.job_timeout=job_timeout;self.poll_interval=poll_interval;self.session=None
    async def start(self):
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.job_timeout,connect=self.connect_timeout,sock_read=min(self.job_timeout,15)),trust_env=False,cookie_jar=aiohttp.DummyCookieJar(),auto_decompress=False,connector=aiohttp.TCPConnector(limit=2))
    async def close(self):
        if self.session:await self.session.close()
    def headers(self):return {'Authorization':'Bearer '+self.token} if self.token else {}
    async def json_request(self,method,path,*,limit=262144,extra_headers=None,**kwargs):
        try:
            async with self.session.request(method,self.endpoint+path,headers={**self.headers(),**(extra_headers or {})},allow_redirects=False,**kwargs) as response:
                if response.status==404:raise RTXError('remote_job_not_found',409,uncertain=method!='GET')
                if response.status in (401,403):raise RTXError('worker_auth_failed',503,uncertain=method!='GET')
                if response.status==429:raise RTXError('worker_busy',503,uncertain=method!='GET')
                if response.status not in (200,201,202):raise RTXError('worker_http_error',503,uncertain=method!='GET')
                if 'application/json' not in response.headers.get('Content-Type',''):raise RTXError('invalid_worker_response',502,uncertain=method!='GET')
                chunks=[];size=0
                async for chunk in response.content.iter_chunked(65536):
                    size+=len(chunk)
                    if size>limit:raise RTXError('worker_response_too_large',502,uncertain=method!='GET')
                    chunks.append(chunk)
                value=json.loads(b''.join(chunks))
                if not isinstance(value,dict):raise ValueError()
                return value
        except aiohttp.ClientConnectorError:raise RTXError('rtx_offline',503,uncertain=False) from None
        except (asyncio.TimeoutError,aiohttp.ClientError):raise RTXError('rtx_timeout_or_connection_lost',504,uncertain=method!='GET') from None
        except RTXError:raise
        except (ValueError,UnicodeError):raise RTXError('invalid_worker_response',502,uncertain=method!='GET') from None
    async def health(self):
        path='/health' if self.mode=='legacy' else '/v1/rtx/health'
        try:
            value=await asyncio.wait_for(self.json_request('GET',path),self.connect_timeout)
            if self.mode=='legacy':
                ready=value.get('ok') is True and value.get('cuda') is True and value.get('device')=='cuda'
                return {'ready':ready,'generate':ready,'analyze':False,'references':False}
            caps=value.get('capabilities',{})
            if value.get('ok') is not True or not isinstance(caps,dict):return {'ready':False,'generate':False,'analyze':False,'references':False}
            return {'ready':True,'generate':caps.get('generate') is True,'analyze':caps.get('analyze') is True,'references':caps.get('references') is True}
        except (RTXError,asyncio.TimeoutError):return {'ready':False,'generate':False,'analyze':False,'references':False}
    async def run(self,job,store):
        ready=await self.health()
        if not ready['ready']:raise RTXError('rtx_offline',503)
        payload=json.loads(job['payload'])
        if not ready.get(job['kind']):raise RTXError('vision_unavailable' if job['kind']=='analyze' else 'generation_unavailable',503)
        if payload['reference_file_ids'] and not ready.get('references'):raise RTXError('references_unavailable',503)
        if not await store.call('begin_remote',job['id']):raise RTXError('remote_job_cancelled',409)
        try:
            return await asyncio.wait_for(self._legacy(job,payload) if self.mode=='legacy' else self._v1(job,payload,store),self.job_timeout)
        except asyncio.TimeoutError:raise RTXError('rtx_job_timeout',504,uncertain=True) from None
    async def _legacy(self,job,payload):
        width,height=map(int,payload['size'].split('x'))
        result=await self.json_request('POST','/generate',limit=24*1024*1024,json={'prompt':payload['prompt'],'width':width,'height':height,'steps':4,'guidance_scale':0.0})
        try:
            if result.get('ok') is not True or result.get('device')!='cuda' or not isinstance(result.get('b64_png'),str):raise ValueError()
            data=base64.b64decode(result['b64_png'],validate=True)
            if len(data)>RESULT_LIMIT:raise ValueError()
            return {'text':'','images':[data]}
        except (ValueError,TypeError):raise RTXError('invalid_image_result',502) from None
    async def _v1(self,job,payload,store):
        form=aiohttp.FormData();references=[]
        data_refs=[]
        for index,fid in enumerate(payload['reference_file_ids']):
            data,meta=await store.call('file_bytes',job['owner'],fid)
            references.append({'field':'reference_'+str(index),'file_id':fid,**{k:meta[k] for k in ['mime_type','size_bytes','sha256']}})
            data_refs.append((index,data,meta))
        metadata={'schema_version':1,'job_id':job['id'],'kind':job['kind'],'prompt':payload['prompt'],'size':payload['size'],'reference_files':references,'request_digest':job['digest']}
        form.add_field('job',json.dumps(metadata),content_type='application/json')
        for index,data,meta in data_refs:form.add_field('reference_'+str(index),data,filename='reference_'+str(index),content_type=meta['mime_type'])
        # Job ID and digest are stable across recovery; this POST is never retried.
        value=await self.json_request('POST','/v1/rtx/jobs',extra_headers={'Idempotency-Key':job['id']},data=form)
        while True:
            if value.get('job_id')!=job['id']:raise RTXError('worker_job_identity_mismatch',502,uncertain=True)
            status=value.get('status')
            if status in {'completed','failed','cancelled'}:return await self._terminal(job,value)
            if status not in {'queued','running'}:raise RTXError('invalid_worker_state',502,uncertain=True)
            await asyncio.sleep(self.poll_interval)
            try:value=await self.json_request('GET','/v1/rtx/jobs/'+job['id'])
            except RTXError as error:raise RTXError(error.code,error.status,uncertain=True) from None
    async def _terminal(self,job,value):
        if value.get('status')=='failed':raise RTXError('remote_job_failed',502)
        if value.get('status')=='cancelled':raise RTXError('remote_job_cancelled',409)
        result=value.get('result')
        if not isinstance(result,dict):raise RTXError('invalid_worker_result',502)
        text=result.get('text','');artifacts=result.get('artifacts',[])
        if not isinstance(text,str) or len(text)>20000 or not isinstance(artifacts,list) or len(artifacts)>3:raise RTXError('invalid_worker_result',502)
        if job['kind']=='generate' and not artifacts:raise RTXError('empty_image_result',502)
        if job['kind']=='analyze' and not text.strip():raise RTXError('empty_vision_result',502)
        images=[]
        for item in artifacts:
            if not isinstance(item,dict) or not safe_id(item.get('artifact_id')) or not isinstance(item.get('sha256'),str) or not isinstance(item.get('size_bytes'),int) or not 0<item['size_bytes']<=RESULT_LIMIT:raise RTXError('invalid_artifact',502)
            try:
                async with self.session.get(self.endpoint+'/v1/rtx/jobs/'+job['id']+'/artifacts/'+item['artifact_id'],headers=self.headers(),allow_redirects=False) as response:
                    if response.status!=200:raise RTXError('artifact_unavailable',502)
                    chunks=[];size=0
                    async for part in response.content.iter_chunked(65536):
                        size+=len(part)
                        if size>RESULT_LIMIT:raise RTXError('artifact_too_large',502)
                        chunks.append(part)
                data=b''.join(chunks)
            except (asyncio.TimeoutError,aiohttp.ClientError):raise RTXError('artifact_download_failed',502) from None
            if len(data)!=item['size_bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:raise RTXError('artifact_checksum_mismatch',502)
            images.append(data)
        return {'text':text,'images':images}
    async def recover(self,job):
        if self.mode!='v1':raise RTXError('legacy_recovery_unavailable',409,uncertain=True)
        try:
            value=await asyncio.wait_for(self.json_request('GET','/v1/rtx/jobs/'+job['id']),self.connect_timeout+5)
            if value.get('job_id')!=job['id']:raise RTXError('worker_job_identity_mismatch',502,uncertain=True)
            if value.get('status') not in {'completed','failed','cancelled'}:raise RTXError('remote_job_still_pending',409,uncertain=True)
            return await self._terminal(job,value)
        except RTXError as error:
            if error.code in {'remote_job_failed','remote_job_cancelled'}:raise
            raise RTXError(error.code,error.status,uncertain=True) from None
