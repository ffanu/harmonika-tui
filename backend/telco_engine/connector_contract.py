"""Bounded read-only adapter transport; no profile/URL/secret ever comes from a model.

Not enabled by catalog registration. An operator must bind a trusted connector to
an exact tenant and pass ownership/freshness smoke before exposing a read handler.
External write dispatch remains disabled in this release.
"""
import asyncio,datetime as dt,json,urllib.parse
from .common import DomainError,require,parse_time,now_iso
READ_SKILLS=frozenset(('olt.optical.power','olt.distance.measure','noc.monitor.uptime','noc.monitor.bandwidth','noc.monitor.cpu','noc.monitor.optical','noc.monitor.temperature','prov.test.connectivity','portal.profile.view','portal.usage.view','portal.invoice.view'))
class ReadConnector:
    def __init__(self,base_url,tenant,skill_ids,service_secret,session,allow_loopback_http=False,max_age_seconds=300):
        u=urllib.parse.urlsplit(base_url)
        require(not u.username and not u.password and not u.query and not u.fragment and u.path in ('','/'),'invalid_connector_endpoint')
        require(u.scheme=='https' or (allow_loopback_http and u.scheme=='http' and u.hostname in ('127.0.0.1','::1')),'secure_connector_endpoint_required')
        require(type(tenant) is str and len(tenant)==64,'invalid_connector_tenant');require(set(skill_ids)<=READ_SKILLS,'connector_write_dispatch_disabled',403)
        require(type(service_secret) is str and len(service_secret)>=32,'connector_auth_required')
        self.base=base_url.rstrip('/');self.tenant=tenant;self.skills=frozenset(skill_ids);self.secret=service_secret;self.session=session;self.max_age=max(1,min(3600,max_age_seconds))
    async def execute(self,tenant,skill,args,request_id):
        require(tenant==self.tenant,'connector_ownership_mismatch',403);require(skill in self.skills,'connector_skill_not_allowed',403)
        require(type(args) is dict and not(set(args)&{'tenant','tenant_id','actor','url','base_url','secret','token','password','command'}),'invalid_connector_input')
        payload={'schema_version':1,'tenant_id':tenant,'skill_id':skill,'request_id':request_id,'input':args}
        raw=json.dumps(payload,allow_nan=False).encode();require(len(raw)<=65536,'connector_input_too_large',413)
        try:
            async with self.session.post(self.base+'/v1/telco/execute',data=raw,headers={'Authorization':'Bearer '+self.secret,'Content-Type':'application/json'},allow_redirects=False,timeout=10) as response:
                require(response.status==200,'connector_unavailable',503)
                chunks=[];size=0
                async for part in response.content.iter_chunked(16384):
                    size+=len(part);require(size<=524288,'connector_output_too_large',502);chunks.append(part)
                result=json.loads(b''.join(chunks))
        except asyncio.TimeoutError:raise DomainError('connector_timeout',504) from None
        except DomainError:raise
        except Exception:raise DomainError('connector_unavailable',503) from None
        require(type(result) is dict and result.get('schema_version')==1 and result.get('skill_id')==skill and result.get('request_id')==request_id,'connector_contract_mismatch',502)
        require(result.get('tenant_id')==tenant,'connector_ownership_mismatch',502)
        require(result.get('status')=='completed' and type(result.get('data')) is dict and type(result.get('source')) is str and 1<=len(result['source'])<=100,'connector_result_unverified',502)
        age=(parse_time(now_iso())-parse_time(result.get('observed_at'))).total_seconds();require(0<=age<=self.max_age,'connector_stale_evidence',409)
        return {'data':result['data'],'source':result['source'],'observed_at':result['observed_at'],'freshness_seconds':age,'scope':'authorized_connector_read','changes_applied':False}
