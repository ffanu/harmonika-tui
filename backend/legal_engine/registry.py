"""Exact source catalog; wrong requested legal labels never become normative knowledge."""
import importlib,json
from pathlib import Path
SOURCE=json.loads(Path(__file__).with_name('catalog_source.json').read_text());IDS=frozenset(x['id'] for x in SOURCE['skills'])
def loaded():
 h={};s={};m=set();l={}
 for name in ('intents','knowledge','analysis','evidence','discovery','updates'):
  try:module=importlib.import_module('.'+name,__package__)
  except ModuleNotFoundError as e:
   if e.name==__package__+'.'+name:continue
   raise
  assert not set(h)&set(module.HANDLERS);assert set(module.HANDLERS)==set(module.SCHEMAS);assert set(module.HANDLERS)<=IDS;assert set(module.MUTATIONS)<=set(module.HANDLERS)
  h.update(module.HANDLERS);s.update(module.SCHEMAS);m.update(module.MUTATIONS);l.update(module.LIMITATIONS)
 for blocked in ('evidence.ocr.extract','evidence.vlm.analyze','evidence.asr.transcribe'):
  h.pop(blocked,None);s.pop(blocked,None);m.discard(blocked)
 return h,s,m,l
def catalog(vision_ready=False,asr_ready=False):
 h,s,m,l=loaded();out=[]
 from .evidence import SCHEMAS as MEDIA_SCHEMAS
 media_mime={'evidence.ocr.extract':['image/png','image/jpeg','image/webp'],'evidence.vlm.analyze':['image/png','image/jpeg','image/webp','video/mp4'],'evidence.asr.transcribe':['audio/wav','audio/mpeg','audio/ogg','video/mp4']}
 for row in SOURCE['skills']:
  id=row['id'];media_active=vision_ready and id in ('evidence.ocr.extract','evidence.vlm.analyze') or asr_ready and id=='evidence.asr.transcribe';active=id in h or media_active
  reason='verified_source_adapter_or_domain_implementation_required'
  if id in media_mime:reason='real_ocr_vision_or_asr_adapter_not_configured'
  if id.startswith('kb.') and id not in h:reason='corresponding_official_corpus_or_authorized_directory_connector_required'
  if id.startswith('uu.') and id not in h:reason='verified_update_workflow_required'
  schema=s.get(id,MEDIA_SCHEMAS[id] if media_active else {'type':'object','properties':{},'additionalProperties':False})
  if id=='evidence.asr.transcribe' and media_active:schema={**schema,'properties':{**schema['properties'],'language':{'type':'string','enum':['id','en'],'default':'id'}}}
  note='; '.join(l[id]) if isinstance(l.get(id),list) else l.get(id,'Not active until the corresponding verified adapter/corpus/implementation exists.')
  if media_active:note='Best-effort local media observation/transcription, owned files only; <=120 seconds for audio/video, sampled video frames only. Human confirmation of all findings/names/dates/numbers is required. No forensic, identity, speaker or legal-fact certification.'
  status='available' if active else 'unavailable'
  if active and id=='kb.advokat.verify':status='degraded';reason='issuer_public_registry_unavailable'
  out.append({'id':id,'layer':row['layer'],'description':id+' — edukasi berbasis sumber resmi; bukan layanan advokat','requested_label_is_not_legal_authority':True,'input_schema':schema,'output_schema':{'type':'object'},'permissions':{'tenant_records':['read','write'] if id in m or media_active else ['read'],'host_shell':False},'readiness':{'status':status,'reason':None if status=='available' else reason},'limitations':note,'supported_mime_types':media_mime.get(id,[]),'execution_target':'legal_tenant','access':['api','tui'],'current_law_certified':False,'licensed_expert_present':False})
 return out
