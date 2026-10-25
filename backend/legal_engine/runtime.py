"""Encrypted tenant/session/request boundary. No mentor may establish legal truth."""
import datetime as dt,hashlib,hmac,json,re,threading
from uuid import uuid4
from pathlib import Path
from .store import Store,Context,canonical
from .common import DomainError,require,validate,now_iso,parse_time
from .registry import loaded,catalog,IDS
from .vault import Vault
from .intents import DISCLAIMER
class Runtime:
 def __init__(self,root,key,sources,vision=None,research=None):
  self.root=Path(root);self.store=Store(self.root/'state.sqlite',key);self.sources=sources;self.handlers,self.schemas,self.mutations,self.limitations=loaded();self.vision=vision;self.vision_lock=threading.Lock();self.asr_ready=bool(vision and getattr(vision,'asr_ready',False));self.research=research
  if self.research is None:
   try:
    from .research_fallbacks import FallbackResearch as Research
    self.research=Research(self.root/'research.sqlite',fingerprint_key=hashlib.sha256(b'legal-research:'+self.store.key).digest())
   except ModuleNotFoundError:pass
  if vision and vision.ready:
   from .evidence import SCHEMAS
   for id in ('evidence.ocr.extract','evidence.vlm.analyze'):
    self.handlers[id]=None;self.schemas[id]=SCHEMAS[id]
  if self.asr_ready:
   from .evidence import SCHEMAS
   self.handlers['evidence.asr.transcribe']=None;self.schemas['evidence.asr.transcribe']={**SCHEMAS['evidence.asr.transcribe'],'properties':{**SCHEMAS['evidence.asr.transcribe']['properties'],'language':{'type':'string','enum':['id','en'],'default':'id'}}}
 def context(self,d,actor,read_only=False):
  c=Context(self.store,d,actor,sources=self.sources,read_only=read_only);c.evidence=Vault(self.store,self.root/'files',actor,c);c.research=self.research;return c
 def recover(self):
  with self.store.db() as d:d.execute("UPDATE requests SET state='interrupted' WHERE state='running'")
 def capabilities(self):
  return {'schema_version':1,'features':{'legal_assistant':True,'legal_sources':True,'legal_source_currentness_certified':False,'legal_licensed_expert':False,'legal_evidence_upload':True,'legal_vision':bool(self.vision and self.vision.ready),'legal_video_visual':bool(self.vision and getattr(self.vision,'video_ready',False)),'legal_asr':self.asr_ready,'legal_ocr':bool(self.vision and self.vision.ready),'legal_evidence_authenticity_certified':False,'legal_filing':False},'document_claimed_skills':221,'document_actual_skills':99,'implemented_handlers':len(self.handlers),'skills':catalog(bool(self.vision and self.vision.ready),self.asr_ready),'disclaimer':DISCLAIMER,'limits':{'input_bytes':262144,'file_bytes':10485760,'accepted_mime_types':['image/png','image/jpeg','image/webp','application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document','video/mp4','audio/wav','audio/mpeg','audio/ogg'],'file_storage_per_tenant':104857600,'requests_per_minute':120,'request_retention_seconds':86400,'case_history_messages':40,'records_per_tenant':2000,'custody_entries_per_file':1000,'media_duration_seconds':120,'video_sampled_frames':2,'media_global_active_jobs':1},'scope':'isolated_legal_tenant'}
 def case_create(self,actor,args,key):
  schema={'type':'object','properties':{'jurisdiction':{'type':'string','pattern':'[A-Z]{2}'},'occurred_on':{'type':['string','null'],'maxLength':10},'perspective':{'type':'string','enum':['pelapor','terlapor','netral']},'topic':{'type':'string','minLength':1,'maxLength':200},'facts':{'type':'string','minLength':1,'maxLength':12000},'questions':{'type':'array','maxItems':20,'items':{'type':'string','maxLength':1000}}},'required':['topic','facts'],'additionalProperties':False};validate(args,schema)
  if args.get('occurred_on'):
   try:date=dt.date.fromisoformat(args['occurred_on'])
   except ValueError:raise DomainError('invalid_incident_date') from None
   require(date<=dt.date.today(),'future_incident_date')
  def fn(c,a):return {'case':c.create('legal_case',{'jurisdiction':'ID','perspective':'netral','occurred_on':None,'questions':[],'status':'open',**a}),'disclaimer':DISCLAIMER}
  return self._invoke(actor,'legal.case.create',args,key,fn,False)
 def invoke(self,actor,skill,args,key):
  require(type(skill) is str and skill in IDS,'unknown_skill',404);require(skill in self.handlers,'skill_unavailable',503);validate(args,self.schemas[skill])
  if skill=='kb.update.sync':return self.sync_source(actor,args,key)
  if skill in ('evidence.ocr.extract','evidence.vlm.analyze','evidence.asr.transcribe'):return self.analyze_image(actor,skill,args,key)
  return self._invoke(actor,skill,args,key,self.handlers[skill],skill not in self.mutations)
 def sync_source(self,actor,args,key):
  self.store.tenant(actor);require(type(key) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}',key) is not None,'idempotency_required')
  with self.store.db() as d:old=d.execute('SELECT 1 FROM requests WHERE actor=? AND idem=?',(actor,key)).fetchone()
  if old:return self._invoke(actor,'kb.update.sync',args,key,lambda c,a:{},True)
  try:result={'source':self.sources.refresh(args['source_id']),'network_refreshed':True,'currentness_certified':False}
  except DomainError as e:result={'status':'degraded','error':{'code':e.code},'network_refreshed':False,'currentness_certified':False}
  return self._invoke(actor,'kb.update.sync',args,key,lambda c,a:result,True)
 def analyze_image(self,actor,skill,args,key):
  # Validate auth/key and current original before replay; network never holds a DB writer.
  self.store.tenant(actor);require(type(key) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}',key) is not None,'idempotency_required')
  with self.store.db() as d:
   c=self.context(d,actor,True);c.get('legal_case',args['case_id']);meta,raw=c.evidence.read(args['file_id']);require(meta['case_id']==args['case_id'],'evidence_case_mismatch',403)
   old=d.execute('SELECT 1 FROM requests WHERE actor=? AND idem=?',(actor,key)).fetchone()
  if old:return self._invoke(actor,skill,args,key,lambda c,a:{},False)
  mode='asr' if skill=='evidence.asr.transcribe' else 'vision'
  require(self.vision_lock.acquire(False),'vision_busy',429)
  try:
   with self.store.db() as d:
    c=self.context(d,actor,True);c.get('legal_case',args['case_id']);meta,raw=c.evidence.read(args['file_id']);require(meta['case_id']==args['case_id'],'evidence_case_mismatch',403)
    cached=[x for x in c.list('legal_media_analysis',where={'file_id':args['file_id'],'sha256':meta['sha256']}) if x.get('adapter_revision','')==getattr(self.vision,'revision','') and x.get('analysis_type','vision')==mode and (mode!='asr' or x.get('language_hint')==args.get('language'))]
    if skill=='evidence.ocr.extract':require(meta['mime_type'].startswith('image/'),'unsupported_ocr_media',415)
   result=cached[0]['analysis'] if cached else self.vision.transcribe(raw,meta['mime_type'],args.get('language')) if mode=='asr' else self.vision.analyze(raw,meta['mime_type'])
   def save(c,a):
    c.get('legal_case',a['case_id'])
    if result.get('analysis_performed') and not cached:c.create('legal_media_analysis',{'case_id':a['case_id'],'file_id':a['file_id'],'sha256':meta['sha256'],'adapter_revision':getattr(self.vision,'revision',''),'analysis_type':mode,'language_hint':args.get('language'),'analysis':result,'legal_fact_verified':False})
    return {**result,'case_id':a['case_id'],'file_id':a['file_id'],'sha256':meta['sha256'],'cached':bool(cached)}
   return self._invoke(actor,skill,args,key,save,False)
  finally:self.vision_lock.release()
 def _invoke(self,actor,skill,args,key,fn,read_only):
  self.store.tenant(actor);require(type(key) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}',key) is not None,'idempotency_required');require(len(canonical(args).encode())<=262144,'input_too_large',413)
  fp=hmac.new(self.store.key,canonical({'skill':skill,'input':args}).encode(),hashlib.sha256).hexdigest();rid=str(uuid4());now=now_iso()
  prepared=None;prepared_error=None
  # Heavy read-only parsers use a WAL read snapshot; never hold the global writer.
  if read_only:
   with self.store.db() as d:
    old=d.execute('SELECT * FROM requests WHERE actor=? AND idem=?',(actor,key)).fetchone()
    if old:
     require(old['digest']==fp,'idempotency_conflict',409);return {**self._view(old),'replayed':True}
    minute=(parse_time(now)-dt.timedelta(minutes=1)).isoformat().replace('+00:00','Z')
    require(d.execute('SELECT count(*) FROM requests WHERE actor=? AND created>=?',(actor,minute)).fetchone()[0]<120,'rate_limited',429)
    d.execute('PRAGMA query_only=ON');d.execute('BEGIN')
    try:prepared=fn(self.context(d,actor,True),args)
    except Exception as e:prepared_error=e
  def run(d):
   old=d.execute('SELECT * FROM requests WHERE actor=? AND idem=?',(actor,key)).fetchone()
   if old:
    require(old['digest']==fp,'idempotency_conflict',409);return {**self._view(old),'replayed':True}
   minute=(parse_time(now)-dt.timedelta(minutes=1)).isoformat().replace('+00:00','Z');require(d.execute('SELECT count(*) FROM requests WHERE actor=? AND created>=?',(actor,minute)).fetchone()[0]<120,'rate_limited',429)
   cutoff=(parse_time(now)-dt.timedelta(days=1)).isoformat().replace('+00:00','Z')
   for r in d.execute("SELECT id FROM requests WHERE actor=? AND created<? AND state!='running'",(actor,cutoff)).fetchall():d.execute('DELETE FROM events WHERE request_id=?',(r[0],));d.execute('DELETE FROM requests WHERE id=?',(r[0],))
   require(d.execute('SELECT count(*) FROM requests WHERE actor=?',(actor,)).fetchone()[0]<1000,'request_quota',429)
   d.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',(rid,actor,key,fp,'running',None,now));self._event(d,rid,'response.created',{'skill_id':skill});d.execute('SAVEPOINT legal_action')
   try:
    if prepared_error:raise prepared_error
    c=self.context(d,actor,read_only);out=prepared if read_only else fn(c,args);require(type(out) is dict,'invalid_domain_result',502);state='degraded' if out.get('status')=='degraded' else 'completed';out={'result':out,'disclaimer':DISCLAIMER,'jurisdiction_applicability_certified':False,'licensed_expert_present':False}
    sealed=self.store.seal(out,'legal.request:'+actor+':'+rid);d.execute('RELEASE legal_action')
   except Exception as e:
    d.execute('ROLLBACK TO legal_action');d.execute('RELEASE legal_action');state='failed';out={'error':{'code':e.code if isinstance(e,DomainError) else 'legal_operation_failed'},'disclaimer':DISCLAIMER};sealed=self.store.seal(out,'legal.request:'+actor+':'+rid)
   d.execute('UPDATE requests SET state=?,result=? WHERE id=?',(state,sealed,rid));self._event(d,rid,'response.error' if state=='failed' else 'response.completed',{'status':state});return self._view(d.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone())
  return self.store.transaction(run)
 def _view(self,r):
  data=self.store.open(r['result'],'legal.request:'+r['actor']+':'+r['id']) if r['result'] else {'error':{'code':'interrupted_requires_review'}} if r['state']=='interrupted' else {}
  return {'id':r['id'],'status':r['state'],'created_at':r['created'],'scope':'isolated_legal_tenant',**data}
 def _row(self,d,actor,id):
  self.store.tenant(actor);require(type(id) is str and re.fullmatch('[0-9a-f-]{36}',id) is not None,'invalid_request_id');r=d.execute('SELECT * FROM requests WHERE actor=? AND id=?',(actor,id)).fetchone();require(r is not None,'request_not_found',404);return r
 def status(self,actor,id):
  with self.store.db() as d:return self._view(self._row(d,actor,id))
 def _event(self,d,id,type,data):
  seq=d.execute('SELECT coalesce(max(sequence),0)+1 FROM events WHERE request_id=?',(id,)).fetchone()[0];d.execute('INSERT INTO events VALUES(?,?,?,?,?)',(id,seq,type,now_iso(),self.store.seal(data,'legal.event:'+id+':'+str(seq))))
 def events(self,actor,id,after=0):
  require(type(after) is int and after>=0,'invalid_sequence')
  with self.store.db() as d:
   self._row(d,actor,id);rows=d.execute('SELECT * FROM events WHERE request_id=? AND sequence>? ORDER BY sequence LIMIT 128',(id,after)).fetchall()
   return [{'event_id':id+':'+str(r['sequence']),'request_id':id,'sequence':r['sequence'],'timestamp':r['timestamp'],'type':r['type'],'data':self.store.open(r['data'],'legal.event:'+id+':'+str(r['sequence']))} for r in rows]
 def case_context(self,actor,id):
  with self.store.db() as d:
   c=self.context(d,actor,True);case=c.get('legal_case',id);messages=sorted(c.list('legal_message',where={'case_id':id}),key=lambda x:x['sequence'])[-40:]
   return {'case':case,'conversation_id':id,'messages':messages,'evidence':c.list('legal_evidence',where={'case_id':id}),'media_analyses':c.list('legal_media_analysis',where={'case_id':id}),'disclaimer':DISCLAIMER}
 def prepare_evidence(self,actor,id):
  if not self.vision or not self.vision.ready:return
  with self.store.db() as d:
   c=self.context(d,actor,True);c.get('legal_case',id);files=sorted(c.list('legal_evidence',where={'case_id':id}),key=lambda x:(x.get('uploaded_at',''),x['id']))
  # At most one uncached image per chat: no 3x40s chain beyond the gateway deadline.
  with self.store.db() as d:analyses=self.context(d,actor,True).list('legal_media_analysis',where={'case_id':id})
  done={(x['file_id'],x['sha256']) for x in analyses if x.get('adapter_revision','')==getattr(self.vision,'revision','')}
  for f in reversed(files[-3:]):
   if f['mime_type'] in ('image/png','image/jpeg','image/webp','video/mp4','audio/wav','audio/mpeg','audio/ogg') and (f['file_id'],f['sha256']) not in done:
    try:self.invoke(actor,'evidence.asr.transcribe' if f['mime_type'].startswith('audio/') and self.asr_ready else 'evidence.vlm.analyze',{'case_id':id,'file_id':f['file_id']},'auto-media-'+id+'-'+f['sha256'][:16]+'-'+hashlib.sha256(getattr(self.vision,'revision','').encode()).hexdigest()[:12])
    except Exception:pass # A failed observer never supplies inferred observations.
    break
 def poll_watches(self,limit=256):
  require(type(limit) is int and 1<=limit<=256,'invalid_poll_limit')
  with self.store.db() as d:
   d.execute('CREATE TABLE IF NOT EXISTS maintenance(key TEXT PRIMARY KEY,value INTEGER)')
   actors=[r[0] for r in d.execute('SELECT DISTINCT actor FROM requests ORDER BY actor')]
   cursor=d.execute("SELECT value FROM maintenance WHERE key='watch_cursor'").fetchone();start=(cursor[0] if cursor else 0)%max(1,len(actors));chosen=[actors[(start+i)%len(actors)] for i in range(min(limit,len(actors)))];d.execute("INSERT OR REPLACE INTO maintenance VALUES('watch_cursor',?)",((start+len(chosen))%max(1,len(actors)),))
  processed=0;failed=0
  for actor in chosen:
   try:
    with self.store.db() as d:watches=self.context(d,actor,True).list('uu_watch')
    if not any(x.get('active') is True for x in watches):continue
    key='scheduled-watch-'+now_iso()[:13].replace('-','').replace(':','')
    out=self.invoke(actor,'uu.alert.new',{'action':'check'},key);processed+=1;failed+=out.get('status')=='failed'
   except DomainError:failed+=1
  return {'processed_tenants':processed,'failed_tenants':failed,'more_tenants':len(actors)>limit,'delivery':'encrypted_in_app_events','external_messages_sent':False}
 def case_get(self,actor,id):
  with self.store.db() as d:return self.context(d,actor,True).get('legal_case',id)
 def file_read(self,actor,id):
  with self.store.db() as d:return self.context(d,actor,True).evidence.read(id)
