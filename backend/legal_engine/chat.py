"""Case-bound contextual source-first conversation, with no ungrounded free-form legal verdict."""
import datetime as dt,re
from .common import require
from .intents import DISCLAIMER,emergency
from .knowledge import search

def answer(runtime,actor,args,key):
 require(type(args) is dict and not(set(args)-{'case_id','message'}) and type(args.get('case_id')) is str and 1<=len(args['case_id'])<=100 and type(args.get('message')) is str and 1<=len(args['message'])<=8000,'invalid_message')
 valid_analysis_ids=set()
 # Validate cached observations against encrypted originals before acquiring any writer.
 with runtime.store.db() as d:
  c=runtime.context(d,actor,True);c.get('legal_case',args['case_id'])
  for item in c.list('legal_media_analysis',where={'case_id':args['case_id']})[-3:]:
   try:
    meta,_=c.evidence.read(item['file_id'])
    if meta['sha256']==item['sha256'] and item.get('adapter_revision','')==getattr(runtime.vision,'revision',''):valid_analysis_ids.add(item['id'])
   except Exception:pass
 def run(c,a):
  case=c.get('legal_case',a['case_id'])
  # Capture only an explicit assertion about the incident, never an arbitrary ID/deadline date.
  match=re.fullmatch(r'\s*(?:tanggal (?:kejadian|peristiwa)|(?:peristiwa|kejadian)(?: terjadi)?)(?:\s+(?:adalah|pada|tanggal))?\s*[:=]?\s*(20[0-9]{2}-[0-9]{2}-[0-9]{2})[.!]?\s*',a['message'],re.I)
  date_note=None
  if match:
   try:day=dt.date.fromisoformat(match[1])
   except ValueError:day=None
   if day is not None and day<=dt.date.today():
    case=c.update('legal_case',case['id'],{'occurred_on':match[1]});date_note='Tanggal kejadian sesuai keterangan Kakak dicatat sebagai '+match[1]+'. Koreksi dengan “Tanggal kejadian: YYYY-MM-DD” bila diperlukan.'
  history=sorted(c.list('legal_message',where={'case_id':case['id']}),key=lambda x:x['sequence'])[-40:]
  seq=history[-1]['sequence']+1 if history else 1
  c.create('legal_message',{'case_id':case['id'],'sequence':seq,'role':'user','content':a['message']})
  urgent=emergency(c,{'text':a['message']})
  questions=[];cites=[];bundle=None
  if urgent['urgent_safety_signal']:
   text='Kak, keselamatan menjadi prioritas. Bila bahaya sedang berlangsung, cari tempat aman dan bantuan darurat setempat jika memungkinkan. Jangan membahayakan diri untuk mengumpulkan bukti.';state='safety_triage'
  elif not case.get('occurred_on'):
   text='Saya bantu menelaah secara netral, Kak. Kapan peristiwanya terjadi? Tanggal kejadian diperlukan agar aturan lama, perubahan, dan ketentuan peralihan tidak tercampur.';questions=['Kapan peristiwanya terjadi?'];state='clarification_required'
  else:
   query=(case['topic']+' '+a['message'])[:500];bundle=search(c,{'query':query,'jurisdiction':case['jurisdiction'],'limit':3})
   required=None;topic=case['topic'].lower()
   for words,titles in [(('perdata','wanprestasi'),('perdata',)),(('cerai','perkawinan','keluarga'),('perkawinan','keluarga')),(('phk','upah','ketenagakerjaan'),('ketenagakerjaan','cipta kerja')),(('konsumen',),('perlindungan konsumen','pelindungan konsumen')),(('pajak',),('pajak',))]:
    if any(word in topic for word in words):required=titles;break
   if required:bundle['matches']=[x for x in bundle['matches'] if any(word in x['title'].lower() for word in required)]
   cites=[x['citation'] for x in bundle['matches']]
   if not cites:
    text='Kak, sumber resmi yang sesuai belum tersedia atau perlu diperbarui dalam basis data ini. Saya belum dapat menyimpulkan pasal atau hasil perkara. Boleh jelaskan tindakan yang terjadi dan pertanyaan utamanya? Penelaahan kasus serius tetap perlu dikonfirmasi dengan advokat.';state='sources_required'
   else:
    text='Kak, berikut kutipan sumber resmi untuk ditelaah bersama. Kutipan ini belum menentukan terpenuhinya unsur, pihak yang benar atau bersalah, maupun aturan yang berlaku pada perkara Kakak.\n\n'+'\n\n'.join('“'+x['quote']+'”\n'+x['title'] for x in cites)+'\n\nTanggal, perubahan aturan, putusan terkait, serta konteks bukti masih perlu diperiksa sebelum menarik kesimpulan. Fakta atau bagian mana yang ingin Kakak telaah lebih lanjut?';state='source_grounded_education'
  observations=[]
  for item in c.list('legal_media_analysis',where={'case_id':case['id']})[-3:]:
   try:
    meta=c.get('legal_evidence',item['file_id'])
    if meta['sha256']==item['sha256'] and item['id'] in valid_analysis_ids:observations.append(item)
   except Exception:continue
  if observations:
   lines=[]
   for item in observations:
    data=item['analysis'].get('observations') or {};description=(data.get('text_detected','')+' '+data.get('description','')+' '+item['analysis'].get('transcript','')).strip()
    if description:lines.append(description[:1500])
   if lines:text='Pengamatan/transkripsi awal lampiran (perlu konfirmasi, bukan bukti keaslian):\n'+'\n'.join(lines)+'\n\n'+text
  observed_files={x['file_id'] for x in observations}
  unanalysed=[x['file_id'] for x in c.list('legal_evidence',where={'case_id':case['id']}) if x['mime_type'].startswith(('image/','audio/','video/')) and x['file_id'] not in observed_files]
  if unanalysed:text='Ada lampiran yang belum dianalisis visual/audio; isinya belum dapat saya pastikan.\n\n'+text
  if date_note:text=date_note+'\n\n'+text
  out={'text':DISCLAIMER+'\n\n'+text,'case_id':case['id'],'conversation_id':case['id'],'state':state,'citations':cites,'questions':questions,'perspective':case['perspective'],'history_messages_considered':len(history),'currentness_certified':False,'applicability_determined':False,'licensed_expert_present':False,'free_form_legal_conclusions_generated':False,'media_observations':observations,'unanalysed_media_file_ids':unanalysed,'sources_checked_before_response':bundle is not None,'safety_signal':urgent['urgent_safety_signal']}
  c.create('legal_message',{'case_id':case['id'],'sequence':seq+1,'role':'assistant','content':out['text'],'citations':cites});return out
 return runtime._invoke(actor,'legal.chat',args,key,run,False)
