#!/usr/bin/env python3
"""Isolated all-99 legal smoke. Credentials on stdin/private file only; logs metadata only."""
import base64,hashlib,io,json,os,sys,time,urllib.request,urllib.error,uuid
from pathlib import Path
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):raise ValueError('smoke_redirect_refused')
def pdf_bytes():
 content='BT /F1 12 Tf 72 700 Td (SYNTHETIC LEGAL FIXTURE document evidence only.) Tj ET';objs=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>','<< /Length %d >>\nstream\n%s\nendstream'%(len(content),content)];out=b'%PDF-1.4\n';offsets=[]
 for n,item in enumerate(objs,1):offsets.append(len(out));out+=('%d 0 obj\n%s\nendobj\n'%(n,item)).encode()
 at=len(out);out+=('xref\n0 %d\n'%(len(objs)+1)).encode()+b'0000000000 65535 f \n'
 for n in offsets:out+=('%010d 00000 n \n'%n).encode()
 return out+('trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n'%(len(objs)+1,at)).encode()
def input_for(item,case,files):
 id=item['id'];s=item['input_schema'];a={};values={'case_id':case,'file_id':files['image'],'evidence_file_id':files['image'],'law_id':'uu_1_2024','source_id':'uu_1_2024','article':'1','text':'Saya meminta edukasi netral tentang bukti elektronik dalam kasus sintetis, bukan kejadian nyata.','query':'informasi transaksi elektronik','identifier':'SYNTHETIC-INVALID-NIA-000000','action':'append'}
 for name in s.get('required',[]):
  schema=s['properties'][name]
  if name=='source_ids':a[name]=['uu_1_2024','uu_1_2023']
  elif name in values:a[name]=values[name]
  elif schema.get('enum'):a[name]=schema['enum'][0]
  else:a[name]='synthetic'
 if id.startswith('evidence.'):
  if id in ('evidence.video.upload',):a['file_id']=files['video']
  elif id in ('evidence.voice.upload','evidence.asr.transcribe'):a['file_id']=files['audio']
  elif id not in ('evidence.image.upload','evidence.ocr.extract','evidence.vlm.analyze'):a['file_id']=files['document']
 if id=='kb.uu.pasal.get':a.update(law_id='uu_1_2023',article='1')
 if id in ('uu.amandemen.track','uu.consolidated.get'):a['source_id']='uu_1_2024'
 if id=='uu.alert.new':a.update(action='subscribe',source_id='uu_1_2024',case_id=case)
 if id=='evidence.chain.custody':a['action']='append';a['note']='Synthetic smoke receipt, not a forensic attestation.'
 queries={'kb.putusan.ma.search':'kesehatan','kb.putusan.pengadilan.search':'Bandung','kb.sirclo.search':'panduan','kb.intl.law.search':'climate change','kb.intl.case.search':'fisheries jurisdiction','kb.article.legal.search':'hukum pidana indonesia','kb.konsil.yudisial':'sanksi hakim','kb.lbh.search':'Bandung'}
 if id in queries:a['query']=queries[id]
 typed={'kb.putusan.mk.search':('92',2024),'kb.pp.search':('18',2021),'kb.perpres.search':('71',2024),'kb.permendagri.search':('74',2022),'kb.perda.search':('8',2025)}
 if id in typed:a={'number':typed[id][0],'year':typed[id][1]}
 return a

def run():
 private=os.environ.get('HARMONIKA_LEGAL_SMOKE_PRIVATE')=='1';secrets_in={} if private else json.load(sys.stdin);actor='api-token:900000000000'+str(int(time.time())%1000000).zfill(6)
 base='http://127.0.0.1:18391' if private else 'https://chat.harmonika.id/v1/legal'
 auth={'X-Harmonika-Legal-Secret':Path('/etc/harmonika-ai/legal-engine.token').read_text().strip(),'X-Harmonika-Principal':actor} if private else {'Authorization':'Bearer '+secrets_in['token']}
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect());report=[];prefix=uuid.uuid4().hex
 if private:
  with opener.open(base+'/healthz',timeout=5) as r:health=json.load(r)
  assert health.get('service')=='harmonika-legal','unexpected_private_service'
 def call(method,path,body=None,*,raw=None,headers=None):
  h={**auth,**(headers or {})};h.setdefault('Content-Type','application/json');h['Idempotency-Key']=prefix+'-'+uuid.uuid4().hex;data=raw if raw is not None else json.dumps(body).encode() if body is not None else None
  req=urllib.request.Request(base+path,data=data,method=method,headers=h)
  try:
   with opener.open(req,timeout=110) as r:return r.status,r.read(2097152),dict(r.headers)
  except urllib.error.HTTPError as e:return e.code,e.read(65536),dict(e.headers)
 def js(method,path,body=None,**kwargs):
  status,raw,h=call(method,path,body,**kwargs);return status,json.loads(raw)
 status,caps=js('GET','/capabilities');assert status==200 and len(caps['skills'])==99 and caps['implemented_handlers']==99,(status,caps.get('error'))
 status,created=js('POST','/cases',{'topic':'SYNTHETIC ITE evidence workshop','facts':'Synthetic fixture only, no real parties or allegations.','jurisdiction':'ID','occurred_on':'2026-01-02','perspective':'netral'});assert status==200 and created['status']=='completed',created;case=created['result']['case']['id'];files={}
 fixture={'image':('fixture.png','image/png',Path('/opt/harmonika-ai/legal-vision-fixture.png').read_bytes()),'audio':('fixture.wav','audio/wav',Path('/opt/harmonika-ai/legal-audio-fixture.wav').read_bytes()),'video':('fixture.mp4','video/mp4',Path('/opt/harmonika-ai/legal-video-fixture.mp4').read_bytes()),'document':('fixture.pdf','application/pdf',pdf_bytes())}
 for kind,(name,mime,data) in fixture.items():
  boundary=uuid.uuid4().hex;raw=('--'+boundary+'\r\nContent-Disposition: form-data; name="case_id"\r\n\r\n'+case+'\r\n--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="'+name+'"\r\nContent-Type: '+mime+'\r\n\r\n').encode()+data+('\r\n--'+boundary+'--\r\n').encode();status,payload=js('POST','/files',raw=raw,headers={'Content-Type':'multipart/form-data; boundary='+boundary});assert status==201,payload;files[kind]=payload['file']['file_id']
 for item in caps['skills']:
  id=item['id'];payload=input_for(item,case,files);start=time.monotonic()
  try:
   status,out=js('POST','/invoke',{'skill_id':id,'input':payload});r=out.get('result',{});row={'skill_id':id,'http_status':status,'status':out.get('status'),'error':out.get('error',r.get('error')),'seconds':round(time.monotonic()-start,2)}
   if 'record_count' in r:row['records']=r['record_count']
   if id in ('evidence.ocr.extract','evidence.vlm.analyze'):row['live_media_check']=r.get('analysis_performed') is True and '7319' in r.get('observations',{}).get('text_detected','')
   if id=='evidence.asr.transcribe':row['live_media_check']=r.get('analysis_performed') is True and all(x in r.get('transcript','').lower() for x in ('dokumen','senin','bukti'))
  except Exception:row={'skill_id':id,'status':'transport_error'}
  report.append(row);print(json.dumps(row),flush=True)
 extra=[]
 for id,payload in [('evidence.vlm.analyze',{'case_id':case,'file_id':files['video']}),('uu.alert.new',{'action':'check','include_events':True})]:
  status,out=js('POST','/invoke',{'skill_id':id,'input':payload});r=out.get('result',{});extra.append({'skill_id':id,'status':out.get('status'),'http_status':status,'analysis_performed':r.get('analysis_performed'),'sampled_frames':len(r.get('frame_offsets_seconds',[]))})
 if private:other={'X-Harmonika-Principal':'api-token:900000000000999999'}
 else:other={'Authorization':'Bearer '+secrets_in['other_token']}
 assert js('GET','/cases/'+case,headers=other)[0]==404
 assert call('GET','/files/'+files['image']+'/download',headers=other)[0]==404
 status,raw,headers=call('POST','/chat',{'case_id':case,'message':'Apa sumber yang perlu dibaca tanpa menyimpulkan kesalahan?','stream':True});assert status==200 and b'response.created' in raw and b'response.completed' in raw and raw.endswith(b'data: [DONE]\n\n')
 failed=[r for r in report if r.get('http_status')!=200 or r.get('status') not in ('completed','degraded') or r.get('live_media_check') is False];assert len(report)==99
 summary={'tested':99,'completed':sum(r.get('status')=='completed' for r in report),'degraded':sum(r.get('status')=='degraded' for r in report),'failed':failed,'extra':extra,'cross_tenant_404':True,'sse_complete':True,'actor':actor if private else None}
 print(json.dumps({'SUMMARY':summary}),flush=True);return 1 if failed or any(r.get('status') not in ('completed','degraded') for r in extra) else 0
if __name__=='__main__':raise SystemExit(run())
